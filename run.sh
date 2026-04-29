#!/bin/bash
cd /workspaces/elite-finds
exec python3 -m bot.main >> bot.log 2>&1
