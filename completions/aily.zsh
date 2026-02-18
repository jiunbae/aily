#compdef aily

_aily() {
    local -a commands
    commands=(
        'init:Set up aily hooks and configuration'
        'status:Show connection and hook status'
        'sessions:List active sessions'
        'sync:Sync messages for a session'
        'logs:Show recent messages for a session'
        'config:Get/set configuration values'
        'doctor:Run diagnostic checks'
        'send:Send a message to a session'
        'attach:Attach to a tmux session'
        'export:Export session messages'
        'help:Show help information'
        'uninstall:Remove aily hooks'
        'version:Show version'
    )

    _arguments -C \
        '--json[Output in JSON format]' \
        '--verbose[Enable verbose output]' \
        '--help[Show help]' \
        '1:command:->cmd' \
        '*::arg:->args'

    case "$state" in
        cmd)
            _describe -t commands 'aily commands' commands
            ;;
        args)
            case "$words[1]" in
                logs|sync|send|attach|export)
                    # Try to complete session names
                    local -a sessions
                    sessions=(${(f)"$(curl -sf http://localhost:8080/api/sessions?limit=50 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    for s in data.get('sessions',[]):
        print(s['name'])
except: pass
" 2>/dev/null)"})
                    _describe 'session' sessions
                    ;;
                sessions)
                    _arguments \
                        '--status[Filter by status]:status:(active idle closed)' \
                        '--host[Filter by host]:host:' \
                        '--agent[Filter by agent]:agent:(claude codex gemini opencode)' \
                        '--json[JSON output]'
                    ;;
                config)
                    local -a subcommands
                    subcommands=('get:Get a config value' 'set:Set a config value' 'list:List all config')
                    _describe 'config command' subcommands
                    ;;
            esac
            ;;
    esac
}

_aily
