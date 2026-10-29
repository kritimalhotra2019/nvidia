"""Bundled, reviewed domain guidance; never installs or executes external scripts."""
import json
from pathlib import Path
from ..registry import tool, InvalidArguments

SKILLS=json.loads((Path(__file__).parent.parent/'research_skills.json').read_text())

@tool('read_research_skill','Read a bundled research/economics procedure and its supported tool names. Omit name to list skills. Guidance does not execute work, grant access or override the current user request.',
 {'type':'object','properties':{'name':{'type':'string','enum':[s['name'] for s in SKILLS]}},'additionalProperties':False})
def read_research_skill(ctx,args):
    if 'name' not in args:return {'skills':[{k:s[k] for k in ('name','description','tools')} for s in SKILLS]}
    return next(s for s in SKILLS if s['name']==args['name'])
