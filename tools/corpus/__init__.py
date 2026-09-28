"""The legal library: fetch official law, parse it into sections, and keep it current.

Standard library only, on purpose. The same code runs on a laptop for a
one-off load and in GitHub Actions for the quarterly refresh, and neither
place should need a pip install to keep the law up to date.
"""
