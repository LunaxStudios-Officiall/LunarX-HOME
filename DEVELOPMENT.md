# Development

The project intentionally uses a small Python standard-library HTTP service and static HTML/CSS/JavaScript. Run:

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_release.py --source .
python3 installer/lunarx_installer.py install --dry-run --source . --admin-user testadmin
```

Do not run lifecycle operations against a production server while developing. Use a disposable Ubuntu environment for installer tests and keep the primary data image outside the source tree.
