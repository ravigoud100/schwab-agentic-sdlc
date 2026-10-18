"""Keep standalone live/manual smoke scripts outside regression collection.

These files execute external models or worktree writes at import time and contain
no pytest test functions. Run them explicitly when their live actions are wanted.
"""
collect_ignore = [
    'test_ollama.py', 'test_gemini.py', 'test_implementation.py',
    'test_repair.py', 'test_codebase.py', 'test_validation.py',
]
