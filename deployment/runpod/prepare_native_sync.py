"""Install verified native Syncthing binaries before renting a GPU."""
import hashlib
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2] / '.runpod/tools/syncthing-v2.1.6'
ASSETS = {
    'syncthing-windows-amd64-v2.1.6.zip': '589098269cabb9a5a1703a934305b4153609d7623e978e1c94f0f49d77ae5e9f',
    'syncthing-linux-amd64-v2.1.6.tar.gz': '524ef4e1df1850b719e2378c8369956f4e5f2ab9a46fdf7f45f2b925012147aa',
}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        archive = ROOT / name
        if not archive.exists():
            pending = archive.with_suffix(archive.suffix + '.pending')
            request = urllib.request.Request('https://github.com/syncthing/syncthing/releases/download/v2.1.6/' + name,
                                             headers={'User-Agent': 'manga-runpod-native-backup'})
            with urllib.request.urlopen(request, timeout=120) as incoming, pending.open('wb') as outgoing:
                for block in iter(lambda: incoming.read(1024 * 1024), b''):
                    outgoing.write(block)
            pending.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Syncthing asset checksum mismatch: ' + name)
        if name.endswith('.zip'):
            with zipfile.ZipFile(archive) as zipped:
                member = next(n for n in zipped.namelist() if n.endswith('/syncthing.exe'))
                (ROOT / 'syncthing.exe').write_bytes(zipped.read(member))
        else:
            with tarfile.open(archive) as zipped:
                member = next(m for m in zipped.getmembers() if len(PurePosixPath(m.name).parts) == 2
                              and m.name.endswith('/syncthing') and m.isfile())
                with zipped.extractfile(member) as incoming:
                    (ROOT / 'syncthing-linux').write_bytes(incoming.read())
            if (ROOT / 'syncthing-linux').read_bytes()[:4] != b'\x7fELF':
                raise RuntimeError('Native Linux binary is not ELF')
        print('VERIFIED ' + name, flush=True)


if __name__ == '__main__':
    main()
