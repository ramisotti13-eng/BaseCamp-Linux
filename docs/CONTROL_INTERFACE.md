# Control Interface & Action Chains

This covers driving BaseCamp from outside the GUI, chaining several actions onto
one key, switching pages and redefining keys (GitHub issues **#16**, **#17**,
**#18**, **#20** and **#63**).

## Callable interface (#20)

While the GUI is running it hosts a small **Unix-domain socket** server. Any
program can connect and send one line of JSON to drive the keyboard or the
DisplayPad, e.g. turn the board red when an appointment approaches, push an icon
to a DisplayPad key when an email arrives, or flip the pad to a page of snippets
when your editor starts.

- **Socket path:** `$XDG_RUNTIME_DIR/basecamp-control.sock`
  (falls back to `/tmp/basecamp-control-<uid>.sock`).
- **Protocol:** one JSON object per line in, one JSON reply line out
  (`{"ok": true, ...}` or `{"ok": false, "error": "..."}`).
- The GUI does not have to be visible. Minimized to tray works the same.

### From the command line

```sh
# Health check
basecamp --ctl '{"cmd":"ping"}'

# Set the Everest 60 side ring to red
basecamp --ctl '{"cmd":"rgb","device":"everest60","args":["side-static","255","0","0"]}'

# Switch the GUI to the DisplayPad tab
basecamp --ctl '{"cmd":"page","page":"displaypad"}'

# Switch the DisplayPad itself to one of your key pages
basecamp --ctl '{"cmd":"dp_page","page":"Editor"}'

# Push an image to Everest Max macro key D3 (button index 2)
basecamp --ctl '{"cmd":"image","device":"everest_max","button":2,"path":"/home/me/icon.png"}'

# Redefine Everest Max key D1 to launch a terminal
basecamp --ctl '{"cmd":"set_key","button":0,"type":"shell","action":"kitty"}'

# Discover tabs, connected devices, DisplayPad pages and the page it is on
basecamp --ctl '{"cmd":"list"}'
```

(`basecamp` = the AppImage / installed launcher. With a source checkout use
`python gui.py --ctl '<json>'`.) The reply is printed to stdout and the exit
code is 0 on `"ok": true`, 1 otherwise, so shell scripts can just test `$?`.

### From any language

Connect to the socket, send a JSON line, read a JSON line. Python:

```python
from shared.ipc import send_command
send_command({"cmd": "rgb", "device": "everest60",
              "args": ["side-static", "0", "255", "0"]})
```

### Commands

| cmd       | fields                                              | effect |
|-----------|-----------------------------------------------------|--------|
| `ping`    | none                                                | health / version |
| `show`    | none                                                | bring the window to the front, also out of the tray |
| `quit`    | none                                                | quit the application, as its Quit entry does (new in 4.0) |
| `list`    | none                                                | GUI tabs, active tab, device presence, DisplayPad pages + current page |
| `page`    | `page`                                              | switch GUI tab (`everest_max`, `everest60`, `makalu67`, `displaypad`, `obs`, `macros`, `plugins`, `settings`, plugin ids) |
| `dp_page` | `page`, `hints` (default `true`)                    | switch the DisplayPad's active key page, by name, by id, or `"prev"`; `"hints": false` keeps the page's box down |
| `rgb`     | `device`, `args[]`                                  | run the device's `rgb …` verb |
| `run`     | `device`, `args[]`                                  | run any device-controller verb (e.g. `["upload","2","/p.png"]`) |
| `image`   | `device`, `button`, `path`                          | upload an image to a key |
| `set_key` | `button` (0-3), `type`, `action`                    | redefine an Everest Max macro key |
| `<service>.<command>` | the command's own fields                | any command of the application's core, see below (new in 4.0) |

`page` and `dp_page` are different things: `page` decides which panel the GUI
shows, `dp_page` decides which twelve keys the physical pad shows.

`device` defaults to the keyboard the application shows (the Everest 60 when
one is plugged in, else the Everest Max). An id other than `everest60` and
`makalu67` means the Everest Max, as it always has.

Fields a 3.x command does not use are ignored, and the command name is not case
sensitive. An unknown command replies `{"ok": false, "error": "unknown cmd 'x'"}`,
a line that is not JSON `{"ok": false, "error": "bad request: …"}`.

### The Everest Max and Monitor Mode

Monitor Mode holds the Everest Max's USB interface for as long as it runs, and
the keyboard takes one claim at a time. Since 4.0, `rgb`, `run` and `image` for
the Everest Max go through the application's Everest Max service: Monitor Mode
is paused, the command runs with the keyboard to itself, and Monitor Mode comes
back afterwards if it was on. Commands queue behind whatever the keyboard is
doing (an upload from the screen, a plugin's D-key picture) instead of failing
with "Failed to claim interface", which is what they did while Monitor Mode ran.

- The reply comes when the command has run, so it can take longer than it did:
  the wait for Monitor Mode to let go, plus the command.
- An upload (`image`, or `run` with `upload` / `upload-main`) rewrites flash and
  takes 45 to 60 seconds. It is given 180 seconds; the old limit of 30 killed it
  half way. Before it claims the keyboard it waits the same moment the upload
  from the screen waits (2.5 s right after Monitor Mode let go, else 0.5 s).
- `basecamp --ctl` waits up to 200 seconds for `image` and `run`, 40 for the
  others. A script of your own should allow for that as well
  (`send_command(obj, timeout=200)`).
- The Everest 60 and the Makalu have no Monitor Mode; their commands run as
  before.

### Service commands (4.0)

Everything the application's core can do is a command named
`<service>.<command>`, the same ones the interface itself uses, and the socket
takes them too. A few:

| cmd | fields | reply |
|-----|--------|-------|
| `devices.state` | none | `present`, `denied` (nodes the udev rule does not open), `keyboard_id` |
| `displaypad.status` | none | `present`, `ready`, `busy`, `page`, `prev_page`, `settings` |
| `displaypad.list_pages` | none | `pages: [{id, name}]`, `current` |
| `displaypad.switch_page` | `page`, `hints` | like `dp_page`, but waits until the switch has been made |
| `displaypad.press_key` | `key` (0-11) | runs what the key on the page on the pad does |
| `displaypad.keys` | `page` (optional) | `page`, `keys: [{idx, image, action, hint}]` |
| `displaypad.set_action` | `key`, `type`, `action`, `page`, `target`, `double`, `extra`, `hint` | one key's action (and its hint for the page box) |
| `displaypad.set_image` | `key` (or `idx`), `path` (none: take the picture away), `page` | `page`, `key`, `image`: a key's picture from a file, any page |
| `displaypad.action_types` | none | `types: [{id, label_key, label, kind, options, ...}]`, `secondary`, `double`: what a key can do, for an editor |
| `i18n.strings` | `code` (optional) | `code`, `strings`: the language's texts, plugins' included |
| `control.ping` | none | like `ping` |
| `everest_max.status` | none | Monitor Mode, clock, main display mode, D-keys, lighting |
| `everest_max.start_monitor` / `stop_monitor` | none | Monitor Mode on or off |
| `everest_max.keybindings` | none | `keys: {<key number>: {function, value, mods?, fkey?, in_keyboard?}}`, `dkeys`, `listener: {state, needed}`, `free_fkeys` |
| `everest_max.set_keybinding` | `key` (the key's SDK number, e.g. 126 Pause), `function`, `value`, `mods` | binds a key like the key binding screen (`function` "default" sets it back); written to the keyboard at once (job `keymap`); refuses a 13th key for the app with `code: no_free_fkey` |
| `everest_max.key_functions` | none | the functions and their choices |
| `everest_max.set_dkey_text` | `button` (0-3), `text` ({text, pos, font, size_pt, bold, italic, underline, color} or null) | text over D1-D4's picture, written with it |
| `profiles.list` | `device` (`everest_max`, `makalu`, `displaypad`) | `profiles: [{id, name, picture_url, program}]`, `active`, `limit` |
| `profiles.activate` | `device`, `id` or `name` | switches like the Profile page (`profiles.activated` event); the device follows, the Everest Max's pictures in the background |
| `profiles.create` / `duplicate` / `rename` / `delete` / `reset` | `device`, `id`, `name` | the Profile page's actions |
| `profiles.set_program` / `set_picture` | `device`, `id`, `path` (none: remove) | program trigger and picture |
| `profiles.export` / `import` | `device`, `id`, `path` | a `.bcprofile` file; `import` also takes a 3.x profile folder |
| `macros.names` | none | `names: {id: name}` |
| `macros.toggle` | `macro_id` | start a macro, or stop a toggle run |
| `obs.execute` | `type` (`scene`, `record`, `stream`), `scene` | an OBS action |

Virtual pads and remote access (4.0):

| cmd | fields | reply |
|-----|--------|-------|
| `vpads.list` | none | `pads: [{id, name, rows, cols, pages, ...}]` (without the keys) |
| `vpads.create` | `name`, `rows` (1-6, default 3), `cols` (1-8, default 4) | `pad` |
| `vpads.rename` / `delete` | `pad`, `name` | a pad's name; or remove it |
| `vpads.set_grid` | `pad`, `rows`, `cols` | the grid size |
| `vpads.keys` | `pad`, `page` (optional, default the current one) | the pad with that page's keys |
| `vpads.set_key` | `pad`, `key`, `page`, `image`, `text`, `text_style`, `action` | sets what is given of one key (`action` is an object with a `type`, as `displaypad.set_action`; `text_style` keeps only `font` (a known family), `pos`, `color` (white, black or #rrggbb), `size_pt` (6 to 18), `bold`, `italic`, `underline`) |
| `vpads.clear_key` | `pad`, `key`, `page` | empties a key |
| `vpads.press` | `pad`, `key` | runs the key's action (chains and double press as on the DisplayPad) |
| `vpads.switch_page` | `pad`, `page` | makes a page the current one |
| `vpads.create_page` / `rename_page` / `delete_page` | `pad`, `page`, `name` | the page actions |
| `remote.settings` | none | `enabled`, `port`, `allow_shell`, `allow_locked`, `lock_watching`, `server_error` |
| `remote.set_settings` | `enabled`, `port`, `allow_shell`, `allow_locked` (each optional) | changes what is given; the server follows |
| `remote.pair` | none | a one-time code (valid 2 minutes) and the addresses a phone opens with it |
| `remote.devices` | none | `devices`: the paired phones |
| `remote.rename_device` / `remove_device` | `id`, `name` | rename a phone; or unpair it (it goes back to the pairing page) |

They reply `{"ok": true, ...}` with what the command returns, or
`{"ok": false, "error": "..."}` when the command refuses (an unknown key, a page
that does not exist) or is given a field it does not take. They are not part of
the 3.x interface and may still change during 4.x; the 3.x commands above stay
as they are.

`show`, `page` and `quit` ask the interface: the core publishes `ui.show`,
`ui.page` and `ui.quit`, and the window does it. A core running without an
interface has no screens, so `list` names none and `page` knows none.

`show` is what the application sends to itself: starting it while it is already
running hands over to the instance that is there and exits, rather than running
a second one that would fight it for the USB devices. It is a normal command,
so a script or a shortcut can raise the window the same way.

### Switching DisplayPad pages from a script (#63)

`dp_page` takes the page **name** you gave the page in the app, the same name
buttons, chain steps and timeouts point at:

```sh
basecamp --ctl '{"cmd":"dp_page","page":"Editor"}'
{"ok": true, "page": 2, "name": "Editor", "changed": true}
```

- `"prev"` goes back to the page you came from, exactly like the `Previous page`
  timeout target.
- A raw page id (`{"cmd":"dp_page","page":2}`) also works. A page name always
  wins, over the `"prev"` keyword as well as over an id, so a page you named
  "prev" or "3" stays reachable.
- A switch from a script is somebody switching pages, so the page's box with
  its note and key hints comes up, as it does for a page key on the pad.
  `"hints": false` switches without it.
- `changed: false` means the pad was already on that page and nothing was done.
  Re-sending the same page is not an error, so a script that fires on every
  window focus does not have to track state.
- An unknown name is rejected and the reply lists the names that do exist:
  `{"ok": false, "error": "dp_page: no page named 'Edtior'", "pages": ["Main", "Editor", "OBS"]}`
- The switch is queued onto the GUI thread and, like a page action on a key,
  waits for a running upload or GIF animation to finish first. `ok` means
  accepted, not "already on screen". Discover the outcome with `list` if you
  need it.

Wrap an application so the pad follows it:

```sh
#!/bin/sh
# ~/bin/code-with-pad
basecamp --ctl '{"cmd":"dp_page","page":"Editor"}'
code --wait "$@"
basecamp --ctl '{"cmd":"dp_page","page":"prev"}'
```

To find the page names a script can use:

```sh
basecamp --ctl '{"cmd":"list"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["displaypad"])'
{'pages': {'0': 'Main', '1': 'Media', '2': 'Editor'}, 'current': 0}
```

## Action chains, multiple actions per key (#17)

A key can run several actions in sequence. Each button's stored action dict may
carry an `actions` list of extra `{ "type": ..., "action": ... }` steps that run
in order right after the primary action, e.g. launch a command **and** switch to
a page:

```json
{
  "type": "shell", "action": "obs --startrecording",
  "actions": [ { "type": "page", "action": "displaypad" } ]
}
```

This is honoured by both dispatchers, the in-process DisplayPad handler and the
Everest Max action daemon.

## Redefine-key action (#18)

A **`set_key`** action type changes another key on press (modal / layered
layouts).

- **Everest Max** (`action` is JSON): `{"button":1,"type":"shell","action":"firefox"}`,
  sent to the GUI over the control socket.
- **DisplayPad** (`action` is JSON): `{"page":0,"key":3,"type":"url","action":"https://…"}`,
  where `page`/`key` default to the current page and the pressed key.

## Actions on plugin / monitor keys, and page hops (#16)

- Plugin-registered action types run on the DisplayPad and the MacroPad, so a
  key showing a plugin or monitor widget can also carry a press action. The
  Everest Max's keys are read by Monitor Mode, a process of its own in which no
  plugin runs: a D-key bound to a plugin's type does nothing.
- A key bound to a type nobody provides any more (its plugin was removed) does
  nothing, on every device. Before 4.0 the DisplayPad and the Everest Max ran
  its stored value as a shell command, a value written for the plugin.
- The **`page`** action type lets any key jump to another tab/page (e.g. a
  System-Monitor key that opens a details page).
- **F1-F12 on the Everest 60:** not implemented. The 60 has no app-assignable
  keys; remapping its real keys to function keys requires the keyboard's
  firmware remap protocol, which isn't reverse-engineered yet (a USB capture of
  Windows BaseCamp doing the remap would be needed, same approach as the side
  LEDs).

## Testing checklist (needs hardware / running GUI)

- [x] `basecamp --ctl '{"cmd":"ping"}'` returns `{"ok": true, ...}` while the GUI runs.
- [x] `dp_page` switches the pad between named pages and re-uploads all twelve
      key images, including the plugin services bound to the target page.
- [ ] `rgb` / `image` / `page` / `set_key` over the socket take effect.
- [ ] An Everest Max key with `type:"page"` switches tabs; `type:"set_key"` redefines another key.
- [ ] A DisplayPad key with an `actions` chain runs every step in order.
- [ ] Existing single-action keys still behave exactly as before (backward compatibility).
