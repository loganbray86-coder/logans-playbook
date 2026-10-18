Local tests for the Playbook Connect add-on (mocked ESPN, fake cookies; needs /workspace/espn_sync/wan_new.json).
  python3 tests/ext/unit.py            # server endpoint tests
  PORT=8812 python3 tests/ext/mock_server.py &  then  node tests/ext/ext.js   # real extension in Chromium (needs a test copy at /tmp/exttest/ext with 127.0.0.1 added)
