#!/usr/bin/env python3
"""Create a reproducible, local preview archive and SHA-256 checksum."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent
NAME = 'load-tester-0.1.0-preview'
FILES = ['README.md', 'requirements.txt', 'start.sh', 'local_service.py',
         'remote_driver.py', 'distributed.py', 'make_certs.sh',
         'build_release.py', 'driver.py', 'driver_nodes.json', 'frontend.py',
         'kafka-client.py', 'metric_data.json', 'orchestrator.py', 'target.py', 'image.png']

def build(output):
    output.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    paths += sorted((ROOT / 'docs').glob('*'))
    paths += sorted((ROOT / 'tests').glob('test_*.py'))
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(paths):
            if not path.is_file(): continue
            data = path.read_bytes()
            info = tarfile.TarInfo(f'{NAME}/{path.relative_to(ROOT).as_posix()}')
            info.size = len(data); info.mode = 0o755 if path.name in ('start.sh', 'make_certs.sh') else 0o644
            info.uid = info.gid = 0; info.uname = info.gname = ''; info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    target = output / f'{NAME}.tar.gz'
    with target.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            compressed.write(tar_bytes.getvalue())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {target.name}\n', encoding='utf-8')
    print(target)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    build(parser.parse_args().output)
