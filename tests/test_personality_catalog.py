"""Keep the documented self-knowledge grounded in the implemented catalogs."""
from pathlib import Path
import json,re
from squipi_worker import tools
from squipi_worker.registry import registry
root=Path(__file__).resolve().parents[1]
profile=(root/'Resources/personality.md').read_text()
names=set(re.findall(r'`([a-z][a-z0-9_-]+)`',profile))
assert set(registry.names())<=names, 'Missing worker capabilities: '+str(set(registry.names())-names)
skills=json.loads((root/'worker/squipi_worker/research_skills.json').read_text())
assert {s['name'] for s in skills}<=names
host=set()
for filename in ['LocalFiles.swift','MacAccess.swift']:
 host.update(re.findall(r'(?:LocalFiles\.)?spec\("([a-z_]+)"',(root/'Sources/Squipi'/filename).read_text()))
agent=(root/'Sources/Squipi/Agent.swift').read_text()
host.update(re.findall(r'"name": "([a-z_]+)"',agent.split('static let appTools',1)[1].split('static let networkTools',1)[0]))
host.add('discover_tools')
assert host<=names, 'Missing app capabilities: '+str(host-names)
assert len(profile.encode())<=16384
assert 'Scout is deferred' in profile and 'after cloud approval' in profile
print(f'Personality catalog verified: {len(registry.names())} worker tools, {len(host)} host tools, {len(skills)} skills.')
