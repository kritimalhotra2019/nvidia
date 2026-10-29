"""Shared research-memory/journal tools for the app and MCP harness."""
from ..registry import tool
from ..research import RESEARCH_TOOLS, ResearchStore


def _handler(name):
    def run(ctx, args):
        ctx.check()
        return ResearchStore(ctx.project.root).call(name, args)
    run.__name__ = name
    return run


for _name, _spec in RESEARCH_TOOLS.items():
    globals()[_name] = tool(_name, _spec["description"], _spec["params"])(_handler(_name))
