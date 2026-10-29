"""NeMo Agent Toolkit component registrations."""
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef, LLMRef
from nat.data_models.function import FunctionBaseConfig

from .state import Job
from .tools import ALLOWED_TOOLS, definitions


class WorkerToolConfig(FunctionBaseConfig, name='squipi_local_tool'):
    job_dir: str
    tool_name: str


@register_function(config_type=WorkerToolConfig)
async def worker_tool(config: WorkerToolConfig, builder: Builder):
    if config.tool_name not in (*ALLOWED_TOOLS, 'read_step_result'):
        raise ValueError('Tool is not allowed in this local prototype')

    async def execute(arguments: dict) -> dict:
        job = Job(config.job_dir)
        try:
            if config.tool_name == 'read_step_result':
                row = job.db.execute('SELECT result FROM calls WHERE key=? AND status=?',
                                     (arguments.get('step_key'), 'done')).fetchone()
                if not row:
                    raise ValueError('Unknown successful step')
                offset = arguments.get('offset', 0)
                if not isinstance(offset, int) or offset < 0:
                    raise ValueError('offset must be a nonnegative integer')
                body = row['result']
                return {'text': body[offset:offset + 8000], 'total_characters': len(body),
                        'next_offset': offset + 8000 if offset + 8000 < len(body) else None}
            return job.call(config.tool_name, arguments)
        finally:
            job.close()

    yield FunctionInfo.from_fn(execute, description='Execute a validated local Squipi tool with durable result tracking.')


class ResearchConfig(FunctionBaseConfig, name='squipi_durable_research'):
    job_dir: str
    llm_name: LLMRef
    tool_names: list[FunctionRef]
    max_steps: int = 12
    seconds: int = 300
    pause_after: str | None = None
    request_seconds: int = 120


@register_function(config_type=ResearchConfig)
async def research(config: ResearchConfig, builder: Builder):
    from .runner import run_job
    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    schemas = definitions() + [{'type': 'function', 'function': {
        'name': 'read_step_result', 'description': 'Read the next page of a saved tool result.',
        'parameters': {'type': 'object', 'properties': {'step_key': {'type': 'string'},
                       'offset': {'type': 'integer', 'minimum': 0}}, 'required': ['step_key']}}}]
    model = llm.bind_tools(schemas, parallel_tool_calls=False)
    functions = {str(name): await builder.get_function(name) for name in config.tool_names}

    async def execute(message: str) -> dict:
        return await run_job(config.job_dir, model, functions, max_steps=config.max_steps,
                             seconds=config.seconds, message=message or None, pause_after=config.pause_after,
                             request_seconds=config.request_seconds)

    yield FunctionInfo.from_fn(execute, description='Resume a persistent local research task using Squipi tools.')
