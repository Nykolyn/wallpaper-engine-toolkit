"""Core engines of the three bundled applications.

Taken verbatim from the standalone apps, with one deliberate behaviour change:
rotator/core.py skips destination folders prefixed with "[protected]" during
rotation (they stay in myprojects untouched). Everything else is byte-for-byte
identical to the originals. Only the GUI layer above it was unified onto Qt.
"""
