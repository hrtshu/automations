# automations

## Install
```sh
git clone https://github.com/hrtshu/automations.git ~/automations && ~/automations/install
```

or

```sh
git clone https://github.com/hrtshu/dotfiles ~/dotfiles && ~/dotfiles/install
```

## hooks for device events
- `device_event_hooks/`
  - `display_added.sh`
    - no arguments
  - `usb_added.sh`
    - first argument: `0411:02DA,0BDA:8153,1A40:0801` (example)
      - `<vendorID>:<productId>` (hex)
  - `usb_removed.sh`
    - the same as `usb_added.sh`
