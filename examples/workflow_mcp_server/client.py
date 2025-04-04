import asyncio
import time
from mcp.types import CallToolResult
from mcp_agent.app import MCPApp
from mcp_agent.config import MCPServerSettings
from mcp_agent.mcp.gen_client import gen_client


async def main():
    # Create MCPApp to get the server registry
    app = MCPApp(name="workflow_mcp_client")
    async with app.run() as client_app:
        logger = client_app.logger
        context = client_app.context

        # Connect to the workflow server
        logger.info("Connecting to workflow server...")

        # Override the server configuration to point to our local script
        context.server_registry.registry["basic_agent_server"] = MCPServerSettings(
            name="basic_agent_server",
            description="Local workflow server running the basic agent example",
            command="uv",
            args=["run", "basic_agent_server.py"],
        )

        # Connect to the workflow server
        async with gen_client("basic_agent_server", context.server_registry) as server:
            # List available tools
            tools_result = await server.list_tools()
            logger.info(
                "Available tools:",
                data={"tools": [tool.name for tool in tools_result.tools]},
            )

            # List available workflows
            logger.info("Fetching available workflows...")
            workflows_response = await server.call_tool("workflows/list", {})
            logger.info(
                "Available workflows:",
                data=_tool_result_to_json(workflows_response) or workflows_response,
            )

            # Call the BasicAgentWorkflow
            run_result = await server.call_tool(
                "workflows/BasicAgentWorkflow/run",
                arguments={
                    "run_parameters": {
                        "input": "Find the closest match to this request."
                    }
                },
            )

            workflow_id: str = run_result.content[0].text
            logger.info(f"Started BasicAgentWorkflow/run. workflow ID={workflow_id}")

            # Wait for the workflow to complete
            while True:
                get_status_result = await server.call_tool(
                    "workflows/BasicAgentWorkflow/get_status",
                    arguments={"workflow_id": workflow_id},
                )

                workflow_status = _tool_result_to_json(get_status_result)
                if workflow_status is None:
                    logger.error(
                        f"Failed to parse workflow status response: {get_status_result}"
                    )
                    break

                logger.info(
                    f"Workflow {workflow_id} status:",
                    data=workflow_status,
                )

                if not workflow_status.get("status"):
                    logger.error(
                        f"Workflow {workflow_id} status is empty. get_status_result:",
                        data=get_status_result,
                    )
                    break

                if workflow_status.get("status") == "completed":
                    logger.info(
                        f"Workflow {workflow_id} completed successfully! Result:",
                        data=workflow_status.get("result"),
                    )

                    break
                elif workflow_status.get("status") == "error":
                    logger.error(
                        f"Workflow {workflow_id} failed with error:",
                        data=workflow_status,
                    )
                    break
                elif workflow_status.get("status") == "running":
                    logger.info(
                        f"Workflow {workflow_id} is still running...",
                    )
                elif workflow_status.get("status") == "cancelled":
                    logger.error(
                        f"Workflow {workflow_id} was cancelled.",
                        data=workflow_status,
                    )
                    break
                else:
                    logger.error(
                        f"Unknown workflow status: {workflow_status.get('status')}",
                        data=workflow_status,
                    )
                    break

                await asyncio.sleep(5)

                # TODO: UNCOMMENT ME to try out cancellation:
                # await server.call_tool(
                #     "workflows/cancel",
                #     arguments={"workflow_id": workflow_id},
                # )


def _tool_result_to_json(tool_result: CallToolResult):
    if tool_result.content and len(tool_result.content) > 0:
        text = tool_result.content[0].text
        try:
            # Try to parse the response as JSON if it's a string
            import json

            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            # If it's not valid JSON, just use the text
            return None


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    end = time.time()
    t = end - start

    print(f"Total run time: {t:.2f}s")
