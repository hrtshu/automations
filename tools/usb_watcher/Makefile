
all: clean build load-plist
build:
	mkdir -p ~/.local/bin
	swiftc -o ~/.local/bin/usb_watcher main.swift
load-plist:
	mkdir -p ~/Library/LaunchAgents/
	cp -f com.shuhei-h.automations.usb-watcher.plist ~/Library/LaunchAgents/
	sed -i'' -e 's|{{HOME}}|$(HOME)|g' ~/Library/LaunchAgents/com.shuhei-h.automations.usb-watcher.plist
	launchctl load ~/Library/LaunchAgents/com.shuhei-h.automations.usb-watcher.plist
unload-plist:
	launchctl unload ~/Library/LaunchAgents/com.shuhei-h.automations.usb-watcher.plist 2> /dev/null
	rm -f ~/Library/LaunchAgents/com.shuhei-h.automations.usb-watcher.plist
clean: unload-plist
	rm -f ~/.local/bin/usb_watcher
	rm -f /tmp/com.shuhei-h.automations.usb-watcher*
