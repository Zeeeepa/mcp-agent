"""
Workflow MCP Server Example

This example demonstrates three approaches to creating agents and workflows:
1. Traditional workflow-based approach with manual agent creation
2. Programmatic agent configuration using AgentConfig
3. Declarative agent configuration using FastMCPApp decorators
"""

import asyncio
import os
import logging
from typing import Dict, Any

from mcp_agent.app import MCPApp
from mcp_agent.server.app_server import create_mcp_server_for_app
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.llm_selector import ModelPreferences
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.parallel.parallel_llm import ParallelLLM
from mcp_agent.executor.workflow import Workflow, WorkflowResult
from mcp_agent.tracing.token_counter import TokenNode

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a single FastMCPApp instance (which extends MCPApp)
app = MCPApp(name="basic_agent_server", description="Basic agent server example")


@app.workflow
class BasicAgentWorkflow(Workflow[str]):
    """
    A basic workflow that demonstrates how to create a simple agent.
    This workflow is used as an example of a basic agent configuration.
    """

    @app.workflow_run
    async def run(self, input: str) -> WorkflowResult[str]:
        """
        Run the basic agent workflow.

        Args:
            input: The input string to prompt the agent.

        Returns:
            WorkflowResult containing the processed data.
        """

        logger = app.logger
        context = app.context

        logger.info("Current config:", data=context.config.model_dump())
        logger.info(
            f"Received input: {input}",
        )

        # Add the current directory to the filesystem server's args
        context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

        finder_agent = Agent(
            name="finder",
            instruction="""You are an agent with access to the filesystem, 
            as well as the ability to fetch URLs. Your job is to identify 
            the closest match to a user's request, make the appropriate tool calls, 
            and return the URI and CONTENTS of the closest match.""",
            server_names=["fetch", "filesystem"],
        )

        async with finder_agent:
            logger.info("finder: Connected to server, calling list_tools...")
            result = await finder_agent.list_tools()
            logger.info("Tools available:", data=result.model_dump())

            llm = await finder_agent.attach_llm(AnthropicAugmentedLLM)

            result = await llm.generate_str(
                message=input,
            )
            logger.info(f"Input: {input}, Result: {result}")

            # Multi-turn conversations
            result = await llm.generate_str(
                message="Summarize previous response in a 128 character tweet",
                # You can configure advanced options by setting the request_params object
                request_params=RequestParams(
                    # See https://modelcontextprotocol.io/docs/concepts/sampling#model-preferences for more details
                    modelPreferences=ModelPreferences(
                        costPriority=0.1,
                        speedPriority=0.2,
                        intelligencePriority=0.7,
                    ),
                    # You can also set the model directly using the 'model' field
                    # Generally request_params type aligns with the Sampling API type in MCP
                ),
            )
            logger.info(f"Paragraph as a tweet: {result}")
            return WorkflowResult(value=result)


@app.workflow
class ParallelWorkflow(Workflow[str]):
    """
    This workflow can be used to grade a student's short story submission and generate a report.
    It uses multiple agents to perform different tasks in parallel.
    The agents include:
    - Proofreader: Reviews the story for grammar, spelling, and punctuation errors.
    - Fact Checker: Verifies the factual consistency within the story.
    - Style Enforcer: Analyzes the story for adherence to style guidelines.
    - Grader: Compiles the feedback from the other agents into a structured report.
    """

    @app.workflow_run
    async def run(self, input: str) -> WorkflowResult[str]:
        """
        Run the workflow, processing the input data.

        Args:
            input_data: The data to process

        Returns:
            A WorkflowResult containing the processed data
        """

        proofreader = Agent(
            name="proofreader",
            instruction=""""Review the short story for grammar, spelling, and punctuation errors.
            Identify any awkward phrasing or structural issues that could improve clarity. 
            Provide detailed feedback on corrections.""",
        )

        fact_checker = Agent(
            name="fact_checker",
            instruction="""Verify the factual consistency within the story. Identify any contradictions,
            logical inconsistencies, or inaccuracies in the plot, character actions, or setting. 
            Highlight potential issues with reasoning or coherence.""",
        )

        style_enforcer = Agent(
            name="style_enforcer",
            instruction="""Analyze the story for adherence to style guidelines.
            Evaluate the narrative flow, clarity of expression, and tone. Suggest improvements to 
            enhance storytelling, readability, and engagement.""",
        )

        grader = Agent(
            name="grader",
            instruction="""Compile the feedback from the Proofreader, Fact Checker, and Style Enforcer
            into a structured report. Summarize key issues and categorize them by type. 
            Provide actionable recommendations for improving the story, 
            and give an overall grade based on the feedback.""",
        )

        parallel = ParallelLLM(
            fan_in_agent=grader,
            fan_out_agents=[proofreader, fact_checker, style_enforcer],
            llm_factory=OpenAIAugmentedLLM,
            context=app.context,
        )

        result = await parallel.generate_str(
            message=f"Student short story submission: {input}",
        )

        return WorkflowResult(value=result)


async def main():
    async with app.run() as agent_app:
        # Add the current directory to the filesystem server's args if needed
        context = agent_app.context
        if "filesystem" in context.config.mcp.servers:
            context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

        # Log registered workflows and agent configurations
        logger.info(f"Creating MCP server for {agent_app.name}")

        logger.info("Registered workflows:")
        for workflow_id in agent_app.workflows:
            logger.info(f"  - {workflow_id}")

        # Create the MCP server that exposes both workflows and agent configurations
        mcp_server = create_mcp_server_for_app(agent_app)

        # Add custom tool to get token usage for a workflow
        @mcp_server.tool(
            name="get_token_usage",
            structured_output=True,
            description="""
            Get detailed token usage information for a specific workflow run.
            
            This provides a comprehensive breakdown of token usage including:
            - Total tokens used across all LLM calls within the workflow
            - Breakdown by model provider and specific models
            - Hierarchical usage tree showing usage at each level (workflow -> agent -> llm)
            - Total cost estimate based on model pricing
            
            Args:
                workflow_id: Optional workflow ID (if multiple workflows have the same name)
                run_id: Optional ID of the workflow run to get token usage for
                workflow_name: Optional name of the workflow (used as fallback)
            
            Returns:
                Detailed token usage information for the specific workflow run
            """,
        )
        async def get_workflow_token_usage(
            workflow_id: str | None = None,
            run_id: str | None = None,
            workflow_name: str | None = None,
        ) -> Dict[str, Any]:
            """Get token usage information for a specific workflow run."""
            if not context.token_counter:
                return {
                    "error": "Token counter not available",
                    "message": "Token tracking is not enabled for this application",
                }

            # Find the specific workflow node
            workflow_node = context.token_counter.get_workflow_node(
                name=workflow_name, workflow_id=workflow_id, run_id=run_id
            )

            if not workflow_node:
                return {
                    "error": "Workflow not found",
                    "message": f"Could not find workflow with run_id='{run_id}'",
                }

            # Get the aggregated usage for this workflow
            workflow_usage = workflow_node.aggregate_usage()

            # Calculate cost for this workflow
            workflow_cost = context.token_counter._calculate_node_cost(workflow_node)

            # Build the response
            result = {
                "workflow": {
                    "name": workflow_node.name,
                    "run_id": workflow_node.metadata.get("run_id"),
                    "workflow_id": workflow_node.metadata.get("workflow_id"),
                },
                "usage": {
                    "input_tokens": workflow_usage.input_tokens,
                    "output_tokens": workflow_usage.output_tokens,
                    "total_tokens": workflow_usage.total_tokens,
                },
                "cost": round(workflow_cost, 4),
                "model_breakdown": {},
                "usage_tree": workflow_node.to_dict(),
            }

            # Get model breakdown for this workflow
            model_usage = {}

            def collect_model_usage(node: TokenNode):
                """Recursively collect model usage from a node tree"""
                if node.usage.model_name:
                    model_name = node.usage.model_name
                    provider = (
                        node.usage.model_info.provider
                        if node.usage.model_info
                        else None
                    )

                    # Use tuple as key to handle same model from different providers
                    model_key = (model_name, provider)

                    if model_key not in model_usage:
                        model_usage[model_key] = {
                            "model_name": model_name,
                            "provider": provider,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        }

                    model_usage[model_key]["input_tokens"] += node.usage.input_tokens
                    model_usage[model_key]["output_tokens"] += node.usage.output_tokens
                    model_usage[model_key]["total_tokens"] += node.usage.total_tokens

                for child in node.children:
                    collect_model_usage(child)

            collect_model_usage(workflow_node)

            # Calculate costs for each model and format for output
            for (model_name, provider), usage in model_usage.items():
                cost = context.token_counter.calculate_cost(
                    model_name, usage["input_tokens"], usage["output_tokens"], provider
                )

                # Create display key with provider info if available
                display_key = f"{model_name} ({provider})" if provider else model_name

                result["model_breakdown"][display_key] = {
                    **usage,
                    "cost": round(cost, 4),
                }

            return result

        # Run the server
        await mcp_server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
