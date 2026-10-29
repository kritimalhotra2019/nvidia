"""Quote conda-forge R launcher paths for macOS Application Support prefixes."""
import shlex
import sys
from pathlib import Path

launcher=Path(sys.argv[1])/'lib/R/bin/R'
lines=launcher.read_text().splitlines(keepends=True)
keys={'R_HOME_DIR','R_SHARE_DIR','R_INCLUDE_DIR','R_DOC_DIR'}
seen=set();patched=[]
for line in lines:
    key,sep,value=line.rstrip('\n').partition('=')
    if sep and key in keys:
        seen.add(key)
        if not value.startswith(('"',"'")):
            if not Path(value).is_absolute():raise SystemExit('Unexpected R launcher path')
            line=key+'='+shlex.quote(value)+'\n'
    patched.append(line)
if seen!=keys:raise SystemExit('Unexpected R launcher layout')
launcher.write_text(''.join(patched))
