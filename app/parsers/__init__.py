"""Infrastructure file parsers.

Each parser knows how to read exactly one technology's files and convert
them into the shared Infrastructure Knowledge Model (app/models/ikm.py).
A parser never needs to know about any other technology or about how its
output is used downstream — see InfrastructureParser in base.py.
"""
