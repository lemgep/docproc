"""Standalone scanned-document processing service.

Pipeline: incoming/<file> -> Code 128 (and other linear) recognition ->
SQLite lookup -> clients/<id name>/<file>
"""
__all__ = ["config", "database", "recognizer", "processor", "logger", "main"]
