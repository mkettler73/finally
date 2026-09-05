"""Pytest configuration and fixtures.

Deliberately minimal: asyncio mode and the fixture loop scope are configured in
pyproject.toml. An earlier `event_loop_policy` fixture here returned
`asyncio.DefaultEventLoopPolicy()` — the policy pytest-asyncio would have used
anyway — and generated a DeprecationWarning on every test (removal in 3.16).
"""
