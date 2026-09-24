# Bundled Python runtime — NOT YET POPULATED

`pythonRunner.ts` looks here FIRST for a self-contained interpreter before
falling back to whatever `python3`/`python` is on the machine's PATH (see
`findPython()`). Right now these folders are empty placeholders — the desktop
app still depends on Python being installed on the machine it runs on. This
file is the exact, remaining step to remove that dependency.

This couldn't be finished in the environment these files were written in: it
has no network access to python.org (only a short allow-list — PyPI, npm,
GitHub — is reachable) and no Windows machine to build/verify a Windows
package on. Both are needed to actually complete this.

## Windows (`python-runtime/win/`)

1. Download the **embeddable package** for the target architecture from the
   official source, e.g. for 3.12.x (x64):
   `https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip`
   (check https://www.python.org/downloads/windows/ for the current 3.12.x
   patch release — use the same minor version the algorithm scripts are
   tested against, currently 3.12).
2. Extract the zip's contents directly into `python-runtime/win/` (so
   `python-runtime/win/python.exe` exists).
3. The embeddable package ships with pip disabled by default. The scheduler
   scripts under `desktop_app/python-runner/` use only the standard library
   (`math`, `datetime`, `random`, `json`, `sys` — see each script's imports
   before adding a new one), so no `pip install` step should be needed. If a
   future script needs a third-party package, vendor it instead of enabling
   pip: `pip install --target python-runtime/win/Lib/site-packages <pkg>`
   run from a machine that has that pip-enabled, then copy the result in —
   don't ship a pip-enabled embeddable distribution itself.
4. Verify: `python-runtime\win\python.exe --version` should print the
   expected version with no other output.

## Linux (`python-runtime/linux/`)

Most Linux desktops already have `python3` installed, so bundling here is
lower priority than Windows — `findPython()`'s system-PATH fallback covers
the common case. If a fully self-contained Linux build becomes necessary
(e.g. for a minimal/locked-down lab image), use a **portable, statically-
linked CPython build** such as the `python-build-standalone` project's
Linux x86_64 release, extracted so `python-runtime/linux/bin/python3` exists.

## After either is populated

Update `pythonRunner.ts`'s `findPython()` to check
`path.join(process.resourcesPath, 'python-runtime', platformDir, exeName)`
first (mirroring `scriptPath()`'s packaged/dev resolution), and add the
matching `extraResources` entry in `package.json`:

```json
{ "from": "python-runtime/win", "to": "python-runtime/win", "filter": ["**/*"] }
```

Then re-run `npm run test:sync` — the existing test doesn't change, but it's
the fastest way to confirm the bundled interpreter still produces the same
scheduling results as the system one.
