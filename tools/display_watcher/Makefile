
all: clean build load-plist
build:
	mkdir -p ~/.local/bin
	swiftc -o ~/.local/bin/display_watcher main.swift
load-plist:
	cp -f com.shuhei-h.automations.display-watcher.plist ~/Library/LaunchAgents/
	sed -i'' -e 's|{{HOME}}|$(HOME)|g' ~/Library/LaunchAgents/com.shuhei-h.automations.display-watcher.plist
	launchctl load ~/Library/LaunchAgents/com.shuhei-h.automations.display-watcher.plist
unload-plist:
	launchctl unload ~/Library/LaunchAgents/com.shuhei-h.automations.display-watcher.plist 2> /dev/null
	rm -f ~/Library/LaunchAgents/com.shuhei-h.automations.display-watcher.plist
clean: unload-plist
	rm -f ~/.local/bin/display_watcher
	rm -f /tmp/com.shuhei-h.automations.display-watcher*
