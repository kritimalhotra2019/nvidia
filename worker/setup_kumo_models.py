"""Download only pinned Kumo/MOMENT files during explicit user setup, not inference."""
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

SOURCE_REVISION = 'af74798bbf7d020d3298f545fb9be383560a18ce'
KUMO_REVISION = '402caa11b7ff585141e912c6743026141f648892'
BACKBONE_REVISION = 'ca58581bc7bea2ebed4e80dc0a3e4b8b609c6ecc'
FILES = [
    ('nvidia/Kumo-Forecast', KUMO_REVISION, 'moment_head_512_6hr.pt', 'kumo', 1404422626,
     '04778bee8196c19af3a3e8dbd93e467a85a25208c63285917185347d9ec71743'),
    ('AutonLab/MOMENT-1-large', BACKBONE_REVISION, 'model.safetensors', 'backbone', 1385501256,
     'a56928052ac6f5d09b97c3834bea6ce3aef9f02b513b5fac98954e7377801572'),
    ('AutonLab/MOMENT-1-large', BACKBONE_REVISION, 'config.json', 'backbone', 951, None),
]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main(root):
    from huggingface_hub import hf_hub_download
    root = Path(root).expanduser().resolve()
    source = root/'source'
    actual = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if actual != SOURCE_REVISION:raise RuntimeError('Kumo source revision does not match the reviewed adapter.')
    receipt = []
    for repo, revision, filename, folder, size, expected_sha in FILES:
        path = Path(hf_hub_download(repo_id=repo, filename=filename, revision=revision, token=False,
                    local_dir=root/'models'/folder, cache_dir=root/'cache/huggingface'))
        actual_sha = digest(path)
        if path.stat().st_size != size or (expected_sha and expected_sha != actual_sha):
            raise RuntimeError('Downloaded model integrity check failed for '+filename)
        receipt.append({'repo':repo,'revision':revision,'filename':filename,'path':str(path),
                        'bytes':size,'sha256':actual_sha})
        print('Verified '+filename+' ('+str(size)+' bytes)',flush=True)
    config = {'enabled':True,'backend':'kumo-ts','python':str(root/'env/bin/python'),
              'package_path':str(source/'Kumo-Forecast'), 'checkpoint_path':str(root/'models/kumo/moment_head_512_6hr.pt'),
              'backbone_path':str(root/'models/backbone'),'device':'cpu','source_revision':SOURCE_REVISION,
              'model_revision':KUMO_REVISION,'backbone_revision':BACKBONE_REVISION,
              'checkpoint_sha256':FILES[0][-1], 'package_version':importlib.metadata.version('kumo-forecast'),
              'inference_tested':False}
    target = root/'config.json'
    if target.exists():
        # Preserve an existing device selection; keep a recoverable prior config.
        prior = json.loads(target.read_text())
        if prior.get('device') in ('cpu','mps','cuda'):config['device']=prior['device']
        (root/'config.previous.json').write_text(target.read_text())
    tmp = root/'config.json.tmp';tmp.write_text(json.dumps(config,indent=2)+'\n');os.replace(tmp,target)
    (root/'setup-receipt.json').write_text(json.dumps({'source_revision':actual,'files':receipt,
        'model_bytes':sum(f[4] for f in FILES),'packages':{name:importlib.metadata.version(name) for name in ['kumo-forecast','torch','pandas','numpy','transformers','huggingface-hub']},
        'inference_run':False},indent=2)+'\n')


if __name__ == '__main__':main(sys.argv[1])
