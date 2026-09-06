# Changelog

## [Unreleased]

- **Monitor Mode survives a suspend, and a fast network no longer takes it down (#101, #102).** Both from @nicandris, both against his own hardware, and both verified here on an Everest Max. Resume resets the keyboard's USB port and re-enumerates it, so the interface the monitor loop had claimed is dead and every write from then on fails; the loop swallowed those and span on for ever, while the panel went on saying it was sending data and the wheel display held whatever number it had when the machine went to sleep. It notices now, either by the gap between `CLOCK_BOOTTIME` and `CLOCK_MONOTONIC`, which is time spent suspended, or by ten failed cycles in a row, and claims the board again. Checked with the forced `USBDEVFS_RESET` he documented: one write failure, then `USB reclaimed after device reset`, and the metrics carry on out of the same process rather than a restarted one. The second is a crash rather than a hang: the send loop capped metric values at 999 and a packet byte holds 0 to 255, and four of the five metrics are percentages so this only ever showed up on the fifth. Network throughput is in MB/s, a 2.5GbE link peaks around 312, and monitor mode died with `ValueError: byte must be in range(0, 256)` on any sustained transfer above 255 MB/s. It also went negative when the interface counters reset, which an `ifdown` or a NIC that re-enumerates on resume will do.

- **Now Playing showed nothing on a sub-page, and never showed a cover anywhere (#99).** @FransM tried it, saw start and stop work while no title or album art appeared, and wondered whether he had done something wrong. He had not, and there were two things behind it. The plugin looked up which key it had been assigned to by reading the stored actions directly, which only ever answers for the main page, so a key on a sub-page was never found: nothing was drawn on it and the icon it had before stayed where it was, which is the "previous icon was not removed" he also reported. It asks the context now, the way the other widget plugins were taught to in #82. The second is that `mpris:artUrl` was being read out of playerctl and then dropped, so no cover was ever drawn although the plugin's own help says it pushes live album art. The cover is now shown on the key and beside the title in the plugin's own screen, for a local file and for the http address a browser reports, fetched once per track rather than on every two second poll, and off the interface thread so a slow fetch cannot freeze the window. Now Playing is at 1.3.

- **The generated icons all follow one naming scheme now (#95).** @FransM noticed while looking through the config directory that the widget plugins write `dp_clock_p1_k2.png` and the application wrote `dp_label_1_2.png` beside them. It was worse than the two forms he saw: folder icons dropped the page entirely on the main page, a legacy name from before sub-pages existed, so the same kind of picture had three spellings. Everything the application draws for a key is now `dp_<what>_p<page>_k<key>.png`, which is what the plugins were already doing. Existing files are renamed on the next start and the stored paths follow, so nothing needs doing by hand; an image you chose yourself is never touched. His second suggestion, moving the pictures into a directory of their own, is worth doing but needs the plugins to move with it, so it is not in this one.

- **A tray icon that lost the notification area stayed lost (#100).** @FransM saw `Failed to dock icon` with an `AssertionError` traceback after a restart. The traceback is pystray's own and it catches it itself, so nothing crashed, but what it does next is the problem: its Xorg backend notes in a comment that it must retry later and then never does. Nothing in it ever docks again, and `run()` neither returns nor raises, so the supervision here, which was written for the case where it does raise (#21), had nothing to react to. The icon was simply gone for the rest of the session with only a traceback to show for it. The helper now watches whether the icon still holds a notification area, and ends the run when it has quietly lost one, which is what makes the existing rebuild happen. It waits until the icon has been docked once, so a desktop that has no notification area yet at login is not mistaken for one that lost it, and it only runs on the backend that has this failure: the AppIndicator backend, which is what a KDE or GNOME session uses, has no such window and is left alone. `tools/test_tray_dock.py` covers it without a display.

- **The permission warning could still name a device node that was fine (#86).** @FransM ran into it again, with the log saying `/dev/hidraw7 (root:root 600)` and his own `ls` on the same node saying `root:plugdev`. He also found how to bring it on: applying all twelve rows in the Button Actions window. That is the missing piece. An upload detaches the kernel driver from an interface and hands it back afterwards, so the kernel builds that interface a fresh hidraw node, and a fresh node belongs to root with mode 0600 until udev has applied the rule to it, which is a window of milliseconds. The check waits for three consecutive scans before it says anything, but it was counting per device, so three different nodes each caught inside their own window added up to a verdict about a device whose entries were readable throughout. It counts per node now, so only a node that is still shut on the third scan is reported, and nothing at all is said while the application is the one churning the nodes.

- **A widget assigned to the page you were already looking at never started (#97).** Reported by @FransM: on a sub-page with no system monitor on it, clearing a key and setting it to Monitor: CPU did nothing at all. Nothing appeared on the pad, nothing in the editor, and no task started, though the key was stored and worked as soon as the page was left and entered again, or the application restarted. Starting and stopping a widget's service was only ever done on a page switch, and assigning a key to the page already on screen is not one. It is now done whenever a key's action changes on the page that is showing, which also covers the other half he found: clearing the last key that used a widget stops it again, where before the thread kept running and painting over a key nobody had assigned it to. The sync is deferred a moment and coalesced, so applying all twelve rows of the actions dialog does not stop a service on one row and start it again on the next.

- **The editor kept the first frame a widget ever drew (#96).** Also @FransM: after assigning a clock, the pad showed the time and the editor took more than a minute to catch up, and changing the clock's value from seconds to date had the same lag. The grid is redrawn when a widget's frame changes what a key shows, which since #90 was decided by the file name. A clock writes one file name every second and changes only its content, so after the first frame there was nothing to notice and the tile stayed as it was until something unrelated happened to redraw it. It now redraws on content as well, at most twice a second per key, which is still far below what a video pushing thirty frames a second would cost.

- **Right clicking a key in the editor now selects it (#98).** @FransM: right click clears a key, but the pane on the right stayed on whichever key was selected before, so clearing K6 while K5 was selected read as though the clear had gone to the wrong key. The key you clear is the one you are working on.

- **Closing the application could abort instead of exit.** Found while running the test suite: shutdown stopped three of the DisplayPad's background threads and then waited a fixed 0.4 s, which is a guess. A worker that had just started opening the device was still inside libusb when that ran out, and libusb ends the process on that rather than returning (`libusb_ref_device: Assertion 'refcnt >= 2' failed`). The workers are now told to stop before anything else happens, and the wait is on the lock the device worker holds while it owns the device, so it lasts exactly as long as it needs to.

- **Stopping Monitor Mode left the Everest's interface 3 with no kernel driver.** The controller detaches usbhid from interface 3 to claim it, and hands it back in a `finally` on the way out. That `finally` never ran, because every caller in the application stops a monitor with `Popen.terminate()`, which is SIGTERM, and Python ends a process on that signal where it stands. So the interface stayed detached after every stop, and after closing the application, until the keyboard was unplugged. The controller now leaves through the same path Ctrl-C already took. The release itself had a second half to it: it only reattached when that same run had been the one to detach, so once a killed run had leaked the interface, every later run saw nothing to give back and left it that way for good. It now hands interface 3 back whether or not it was the one that took it, which also repairs a keyboard that is already in that state, and a reattach that fails says so on stderr instead of being swallowed, which is what let this go unnoticed for as long as it did. `tools/test_everest_release.py` pins both halves and needs no keyboard.

## [3.1.3] - 2026-08-29

- **The Everest Core is the same keyboard as the Max, and the application has never known the difference.** The Max is the Core with a numpad and a media dock attached, and both report product id `0x0001`, so nothing outside the keyboard can tell them apart. BaseCamp Linux has only ever been used with a Max, so it offers the numpad key actions and the main display to everyone, and on a Core those controls have nothing behind them. The keyboard itself knows: `11 14` answers with `FW_EXTEND_INFO`, which carries `byMMDockPlug` and `byNumpadPlug`, and the monitor loop already sends that command several times a second as a keepalive and throws the answer away. Read out of `SDKDLL.dll` (`GetExtendInfo`) and checked against an Everest Max here, where both flags read 1 and the rest of the struct lines up: the dock colour, the two timeouts and the five profile slots all come out where the struct says they should. `tools/everest_probe.py` is a standalone script for Everest owners that reports what their keyboard says is attached, so the screen can stop offering parts that are not there. It reads only, goes through `/dev/hidraw`, and never detaches a kernel driver.

- **The main display's Volume mode shows the system volume.** It showed the firmware's own count of wheel turns, so it drifted from the real level and ignored every change made anywhere else: the media keys, an application's own slider, or a switch to another output device. The board does take the level, on a command of its own, `11 83` followed by the level 0 to 100, found by walking the command space against real hardware. It is not a metric slot: `11 81` accepts exactly six of those, 0 to 4 for CPU, GPU, disk, network and RAM and 5 for APM, which the monitor loop had never filled, and answers `ff aa` to anything else. Monitor Mode reads the mixer, `wpctl` where WirePlumber runs and `pactl` otherwise, and pushes the level beside the metrics; the default sink is re-resolved on every read, so whichever output is selected is the one reported. Muted reads as 0, an over-amplified level clamps to 100, and a mixer that cannot be read skips the push rather than sending a wrong number. The level is followed through `pactl subscribe` instead of polled, because the loop pushes several times a second and a value even half a second old is pushed over what the firmware displayed the moment the wheel turned, so the number visibly snapped back to the old one. That subscription runs with `LC_ALL=C`, since the event lines are translated and the German ones say `Ereignis` where the filter looks for `Event`. The monitor card gained a Volume meter, so the application shows the same number the keyboard is being sent. The parsing of both mixers and the event filter are pinned by `tools/test_volume_parse.py`, which needs no hardware.

## [3.1.2] - 2026-08-29

- **The interface no longer starts in German on a machine that is not (#92).** Reported by @Eirikur on Linux Mint, who could not find how to switch it and had grepped the code looking. The default was a hardcoded `"de"`, so a first start with no language chosen came up German wherever it was, and the way out of that is a setting labelled in German. It follows the system locale now, in gettext's order (LANGUAGE, LC_ALL, LC_MESSAGES, LANG), and falls back to English rather than German for a locale there is no translation for. Anyone who has picked a language keeps it: that choice is stored and still wins.
- **Wave and Tornado were being sent on the wrong command (#85).** Found by @Thargorrr, who ran the probe's lighting pass: the backlight and all three static colours lit his pad, Wave stayed dark, and the pad acknowledged every packet either way. Reading the Windows software afterwards showed why. Base Camp routes exactly two effects, Colorwave and Tornado, through `ChangeBlockEffect` rather than `ChangeEffect`. It is the same `14 2C` command, but the 62 byte struct behind it is a different one: `byBlockNum` sits after `byWidth`, which moves the colours one byte along, and the two fields the driver was sending as unused carry real values, a width of 2 and a direction that is mapped rather than passed through (Wave `0,1,2,3` go out as `6,2,4,0`, Tornado `0,1` as `10,9`). The wrapper in the DLL refuses any effect index other than 4, 5 and 7, which is the other half of the evidence. The MacroPad screen gained a Direction control for those two effects.
- **The custom lighting sequence was the wrong way round (#85).** Same run: the per key colours and the custom effect stayed dark. The two packets the driver sends are byte for byte what the SDK sends, which took a disassembly of `SwitchToCustomizeEffect` to establish, so the packets were never the problem. The order was: Base Camp switches to the custom effect first and sends the colours after, and the driver did it the other way round. Corrected. It is probably still not the whole sequence: the service also writes the customize table back with `SetCustomizeTable`, whose wire format is not worked out, so this wants another run on hardware before anyone calls it fixed.
- **The two lighting structs do not use the same colour type (#85).** @Thargorrr ran the probe again: the block form of Wave lit his pad where the old form left it dark, so the command was right, but it lit white rather than the red it was sent. `EffData` carries `FWColor`, three bytes of red, green and blue. `BlockData` carries `FWBColor`, four: a leading `pos` and then the three. The colour was going out one byte early, so the pad read a position of 255 and a colour of black. Each struct adds up to exactly 62 bytes only with its own type, which is the arithmetic that settles which is which, and the SDK writes 100 into the first colour's `pos` and `0xFF` into the second rather than leaving them at zero.
- **The speed a person sets is not the number the pad wants (#85).** @Thargorrr's fourth run reported breathing as "from off to red very slowly, then stays static red", which is a 60 arriving where the firmware expects a 3. `getUISpeed` in the Windows service is a one line pass-through and that is what made this look settled, but both SDK wrappers run the value through a table before the packet leaves. The slider has five positions, the thresholds are the same for every effect, and the values run downwards: Wave and Tornado get 10 down to 6, Breathing and the Reactive effects 5 down to 0, Matrix 20 down to 0, Yeti 10 down to 0. Static, Custom and Off carry no speed at all.
- **Reactive B was on the wrong command entirely (#85).** It was the one thing on his pad that did nothing, and the reason is the same split that Wave turned out to have: `ChangeBlockEffect` accepts effects 4, 5 and 7, and `ChangeEffect` accepts 0, 1, 3, 6, 9, 11 and 12. Reactive B is in the first list and not the second. It has no direction and both of its colour positions are marked unused, which is what the wrapper sets for that effect.
- **Wave stood still because of one byte (#85).** @Thargorrr's third run: the colour fix landed, Wave lit red instead of white, Tornado came up red and rotating, and the customize table made the twelve per key colours appear. Only Wave still did not move. The reason is that the struct is built in one place and rewritten in another: `getChangeBlockEffect` in the service sets `byWidth` to 2, and the SDK's own wrapper overwrites it with 0 for both of these effects before the packet goes out. Tornado rotated with the 2 anyway, Wave did not, and a single block two wide is a block that does not travel. Reading the builder and not the wrapper is what cost the extra round. A two colour Wave turns out to be a different shape again, the pair repeated and spaced along the strip at 25, 50, 75 and 100 with four blocks, which is now built that way too.
- **The custom path was missing a whole command (#85).** His second run had the corrected order and the per key colours still stayed dark, so the order was necessary and not sufficient. What the driver never sent at all is `SetCustomizeTable`, the twelve byte table that says which effect each key runs. It is `14 A0`, chunk in byte 2 and the table from offset 4, read out of the export's worker in the SDK. All zeroes is Static on every key, which is what should make the uploaded colours visible. Base Camp also writes flash twice in that sequence; that stays on the Save to pad button rather than happening behind the person's back on every colour change.
- **The probe sends both forms of Wave and asks which one lit up.** Guessing once and shipping it is what produced the dark pad; `tools/macropad_probe.py --lighting` now sends the old packet and the new one, and Tornado, and asks about each separately.

## [3.1.1] - 2026-08-27

- **The MacroPad has a screen.** The call for testers in #85 was answered by @FrankieDedo and @Thargorrr, who ran `tools/macropad_probe.py` on their pads, on Ubuntu and on Arch, on units with different firmware builds, and sent the reports back. Their captures agree byte for byte, which is what a measurement needs before anyone builds on it, and they turned out to be the DisplayPad's layout exactly: `data[0] == 0x01`, M1 to M7 in byte 42 from bit 1, M8 to M12 in byte 47 from bit 0, gap and unused bit included. That was the last piece the Windows software could not be read for, and with it the driver from 3.1.0 became a device you can use. The screen sits in the sidebar the moment the pad is plugged in and has two halves. Key actions: the twelve keys as they are printed on the pad, six across and six below, click one and give it a shell command, a URL, a folder, a program, an OBS action, a macro, a key combination or a piece of text, plus whatever action types the installed plugins register. Lighting: all eleven effects, brightness, speed, one or two colours depending on what the effect reads, and a Custom mode that gives every key its own colour, with a strip under each key in the grid showing what it will get. "Save to pad" writes the state into the pad's own flash so it survives a replug with the application closed. One thread owns the pad's single command interface and does both jobs, sending commands and reading key presses, which is the arrangement the DisplayPad needed for the same reason (#26 to #28). What is still unmeasured is the lighting: neither tester ran the probe's opt-in `--lighting` pass, so those packets are still SDK derived only.
- **Every device works with either `hid` package (#85).** Two unrelated projects on PyPI install a module called `hid`: one gives you the class `hid.Device`, the other the older `hid.device()`. The application only knew the first, and distributions mostly ship the second, so a source installation on Ubuntu or Arch reached no Mountain device at all and said `module 'hid' has no attribute 'Device'`. Both MacroPad owners who answered the call for testers were on that side of the split, which is how it came to light. Nothing in the application touches `hid` directly any more; it goes through `shared/hid_compat.py`, which opens either flavour and hands back one API. Verified on real hardware with both packages installed in turn: a Makalu 67 returns the same DPI table through either, and a timed read on the Everest Max vendor collection comes back empty after the timeout on either. AppImage installations were never affected, since the AppImage bundles the first package.
- **The button-action editor keeps what was typed into it (#87).** Reported by @FransM: a value typed into a key's field and then saved with the button, without clicking another field first, was gone. Two causes, both in the editor. The value fields for OBS, macros, Hue and plugin widgets are dropdowns, and a dropdown hands over what was typed into it only when the list is used or the widget loses focus; clicking a button moves no focus, and forcing one first does not help, because a focus event is not an idle task and is still in the queue when the save reads the value. The save now asks the widget instead of hoping. The second cause was worse and hit every field: saving one key makes the panel bring the other editor in step (#84), which re-reads all twelve rows from storage, so saving all twelve wrote the first row and then reverted the eleven the loop had not reached. Measured on the running application: before, typing into three keys and pressing the button stored one of them; after, all three. This dialog no longer re-reads itself while it is the one saving, and the sync with the key inspector still works in both directions.
- **A device that is perfectly readable is no longer reported as locked (#86).** Reported by @FransM, with an `ls` that contradicted us: the screen said there was no access to `/dev/hidraw11` while the node was plain `crw-rw-rw-`. The check asks whether each of the device's nodes can be read and written, and the answer for a path that does not exist is also no. The DisplayPad re-enumerates by itself, so the node listed a moment earlier can be the one it has just dropped, and that was being reported as a permission problem, which puts a full "cannot be opened" notice over a device that is working. A node that has gone away is now simply not a node any more. The log line that goes with a real denial now names owner, group and mode of each node, so the next report of this kind can be told apart from a false one at a glance.
- **A widget's picture reaches the key grid, not only the pad (#90).** Reported by @FransM: a key cleared and then given a clock came back blank in the editor after a restart. The grid draws whatever image the key holds, and a plugin writes its frame in there itself, but nothing ever told the grid to redraw. So the pad showed the clock while the editor showed the icon stored for that key, which for a key that was cleared first is the blank one. The tile is redrawn when a frame changes what the key shows, and only then, so a video pushing frames does not cost a redraw per frame.
- **A widget's picture belongs to one page, and is never read half written (#88, #89).** Both reported by @FransM. The widget plugins named their frame files after the key index alone, so the same widget on the same key of two pages wrote to one file and whichever rendered last decided what the other page showed; they also registered every frame against Main whatever page they were actually on. Pipe Text already did this right, and the other four now follow it: the page is part of the name and the frame is registered against the page it was drawn for. The second report was a frame caught while it was being written, which the upload worker sees as "cannot identify image file", because a save writes straight onto the destination. They are written beside it and moved into place instead. Philips Hue also got the page-aware action lookup the others were given in #82, which it had been left out of. Update them from the Plugins screen (Clock 1.2.3, System Monitor 1.3.2, Philips Hue 1.1, Video 1.0.2, Pipe Text 1.2).
- **The udev rule covers the MacroPad.** `99-mountain.rules` listed every Mountain product id except `0x0008`, so the pad's device node would have stayed root only even with the right Python package installed. The probe now prints the exact one line rule when it is refused access.

## [3.1.0] - 2026-08-12

- **The Mountain MacroPad is reverse engineered, and we are looking for one owner to finish it.** The MacroPad is the 12 key pad with M1 to M12 keycaps and per-key RGB, no displays. It has always been a separate device in the Windows software, with its own SDK and its own manual, and it turns out to share its command layer with the DisplayPad we already drive. Read out of that software: VID `0x3282` PID `0x0008`, a vendor HID collection on usage page `0xFF00` with 64 byte reports, five profiles, nine effect slots, and the packets for all eleven lighting effects, per-key colours, profile switching, key remapping, shortcuts and flash saving. The reading was checked rather than assumed: the same analysis applied to the DisplayPad's SDK reproduces, byte for byte, the two commands our working DisplayPad driver sends to real hardware, and the DisplayPad on the bench reports exactly the transport the analysis predicted. What no amount of reading can settle is the report the pad sends when a key is pressed, because that decoding sits inside a helper class in the vendor DLL and nobody here owns the hardware. `tools/macropad_probe.py` collects it: a single standalone file that lists the interfaces, sends the handshake, and walks the tester through pressing M1 to M12 while recording the raw reports. It writes and saves nothing on the device. The protocol layer, `devices/macropad/controller.py`, and a check that pins its packets to the documented bytes without any hardware, `tools/test_macropad_protocol.py`, ship with this release. There is no MacroPad screen yet: it comes with the first probe file we receive.
- **A widget assigned on a sub-page now runs on that sub-page (#82, #70).** Reported by @FransM, who assigned a clock to K6 on his F-key page and got nothing there, while the clock from Main kept painting whatever key sat at its index on the page he was actually looking at. Three plugins asked the application for "the button actions" without saying which page, and that call answers with the main page. So a widget only ever saw Main: it could not find the key it had been given on a sub-page, and it kept pushing frames at the index it held on Main. Measured against a real configuration with assignments on pages 2 and 3: the call every widget was making returns Main's single action and nothing else. Clock, System Monitor and Video now ask for the page that is on the pad, which is what Pipe Text already did and why that one was never affected. Update them from the Plugins screen. The application-side guard that keeps a widget's frame off a key it does not own got a related fix: it treated a page with nothing assigned as "cannot tell" and switched itself off, when an empty page is precisely the one where no widget frame belongs.
- **Changing a key's action type clears the old value (#84).** Reported by @FransM: a key set to Keypress with `F6`, switched to Clock, kept `F6` as the clock's value. The actions dialog has cleared it since #9; the key inspector beside the grid did not, and it was not just showing the stale value, it was saving it under the new type.
- **The key inspector and the actions dialog show each other's edits (#84).** They are two views of the same key and neither noticed when the other changed it, so whichever you looked at second was wrong. Both save through one function, which is now also where they are told to re-read.
- **The Now Playing fix from 3.0.5 finally reaches installed copies.** A bundled plugin is only refreshed when its version number goes up, and the #49 environment fix went into Now Playing without one, so every existing installation kept running the code from before it, in which `playerctl` inherits the AppImage's library paths and quietly does nothing. Version raised so the refresh runs.

## [3.0.9] - 2026-08-07

- **A dropdown opens inside the window (#66).** Reported by @FransM: an open dropdown stayed painted over whatever he switched to. A Tk menu takes a *global* grab while it is posted, which is how it keeps receiving the clicks that land outside it, and under Wayland that means the compositor activates the other application without this process being told: no FocusOut, no Deactivate, no pointer event, so there is no event left to close the list on. What went out in 3.0 closes it where the session does report the change, which is X11 only, and a workaround for one session type is not a fix. Every option menu and combo box now draws its list inside the window as an ordinary widget, so it is stacked with the window and goes behind the other application together with it. Measured on the same control: before, a menu window that holds a global grab and reaches past the bottom edge of the application; after, a frame inside the window and no grab at all. The list can also do what a menu could not: the current value is marked, the arrow keys, Home, End and Page up and down walk through it, Return picks, Escape closes, a click next to it or a scroll underneath closes it, and a list too long for the window scrolls instead of running off the screen. Six hundred entries open in 34 ms. All forty-one dropdowns in the application changed over without a line at any of them.
- **The right-click menu in text fields went with it.** Cut, copy, paste and select all sat in a Tk menu of their own and therefore had exactly the same problem.

## [3.0.8] - 2026-08-05

- **A key that runs a macro says which macro.** Since 3.0 it showed `CTkComboBox`, the name of the widget itself, on both the Everest Max numpad keys and the DisplayPad keys. The dropdown asked the Macros screen for the names, and that screen is only built the first time it is opened, so on every start, which begins on a device screen, there were no names to be had and the widget kept its placeholder. The names now come from the saved macros while that screen does not exist, and with no macros at all the dropdown says so instead of leaving the placeholder on screen. The assignment itself was never affected: the key ran the macro it was set to the whole time.
- **New screenshots in the README**, taken on 3.0 with all three devices attached. Every picture there still showed the pre-3.0 window with the tab bar.

## [3.0.7] - 2026-08-05

- **The tray icon is back.** Since 3.0.0 there was no icon at all, so minimising the window put it out of reach and only the launcher brought it back. The page submenu added to the tray menu in 3.0.0 is the cause: its handlers carried the page name in a third parameter with a default value, and pystray counts a handler's parameters, defaults included, and refuses anything above two. It raised while the icon was being docked, inside the menu that is built lazily at that moment, so nothing was printed and the tray helper sat there running with no icon. Every tray that could reach the application was affected, which is every tray, since the submenu is only added when the page list can be read. The page now travels in a closure, and building the submenu can no longer take the icon down with it: a failure in there is reported and the icon keeps its other entries. Verified against the real AppImage, where the icon registers with the desktop again.

## [3.0.6] - 2026-08-05

- **A widget's picture no longer becomes a key's icon (#69, #70).** Found by @FransM, who tracked the last of these reports down to his own `displaypad_pages` file: a key on his F-key page carried `dp_mon_4.png`, a System Monitor frame, and it came back after every switch until he edited the name out of the json by hand. The frame stamping shipped in 3.0.1 keeps a stale frame off the pad, but not out of the configuration. A plugin writes its frame into the panel's image maps as well as pushing it, so that the key grid and a full page re-upload show the widget. Those maps are what the application saves, so a frame written in the moment between a page switch and the plugin's `stop()` taking effect was saved as the icon of whatever key sat at that index on the new page. A plugin running on a sub-page did the same to Main, whose map every plugin writes directly. Both outlived the plugin, because by then they were on disk. Measured on the running application, six page switches at different points in a plugin's painting cycle: before, the F-key page ends up holding the plugin's frame and Main loses the assigned icon; after, both keep what they were given, while the widget still paints on the keys it owns. The panel now keeps every frame it is handed out of what it stores and out of what it loads for another key, and any assignment made in the application takes the key back.

## [3.0.5] - 2026-08-04

- **A program started from a key no longer loads the AppImage's libraries (#49).** Reported by @rebell218, whose Plasma System Monitor kept coming up with empty Processes and Applications pages. Measured on the real AppImage: with the 3.0.4 environment, 63 of the libraries plasma-systemmonitor loads came out of our bundle instead of the system, among them glib, gio, gobject, dbus and systemd. With the fix, none do. Two holes let that happen. `PANGO_LIBDIR`, `PANGO_SYSCONFDIR` and `GIO_MODULE_DIR` are set by PyInstaller's runtime hooks from inside Python rather than by the bootloader, so they never appear on the list of names the sanitiser knew, and they leaked from a completely clean start. And after an in-app update the application re-executed itself with its own environment, so the new instance's AppImage runtime stacked a second mount point onto `LD_LIBRARY_PATH` and `XDG_DATA_DIRS` and recorded the pair as the pre-launch "original" value, which the sanitiser then faithfully restored. From that point on every launched program inherited the mount of an instance that no longer existed, and one update was enough to get there. The sanitiser now works by location instead of by name: any path element that lives inside an AppImage mount or a PyInstaller bundle is removed from every variable, whichever mount it belongs to, and a variable with nothing left is dropped. The restart after an update starts from a sanitised environment, the way the desktop launcher would start it. A program launched from a key now receives 14 variables, none of them ours.
- **Two more programs we start got the same treatment.** `update-desktop-database` after installing the autostart entry, and the `playerctl` and `pactl` calls in the Now Playing plugin. All three are glib programs, which is exactly the kind that fails when it finds our bundled glib first.

## [3.0.4] - 2026-08-03

Four reports from @FransM, all in the interface. Source-overlay patch.

- **A device that is briefly unreadable no longer raises an alarm (#80).** A `/dev` node exists for a moment before udev has applied our rule to it, and the DisplayPad re-enumerates on its own, so the permission check added in 3.0.1 could fire during an ordinary page switch and put a full "the device is here, but cannot be opened" notice on screen. A denial now has to survive three consecutive scans, roughly fifteen seconds, before it is reported; access returning clears it at once. A rule that really is missing is still named.
- **Fullscreen is its own button (#78).** It sits under the keys and goes straight to the file picker, instead of being reachable only by opening the assign-images window first and walking past twelve key slots. The header button is called "Assign images" now, after the window it actually opens, which also settles the naming mismatch from #74. Both share one implementation of the splitting.
- **A page action says what its text field is for (#50).** The target picker comes first and the caption entry after it, and the placeholder says "Key caption" instead of "Page name". The field is the text drawn on the key, which is not the page it goes to and not the name of one.
- **Plugin icons in the list (#79).** A plugin that ships `icon.png` showed it in the detail pane but not in the list on the left, which is where you look to find one.

## [3.0.3] - 2026-08-03

- **Colours can be applied while the CPU monitor runs.** The monitor holds the keyboard's USB interface, so the application stops it before any command that talks to the keyboard and starts it again afterwards. It found the monitor by looking at the screen that was open, which was the same thing while the colour editors were windows on top of their device screen. Since 3.0 they are screens of their own, so from inside the per-key editor the lookup found the editor, which owns no monitor, and the monitor kept the interface: every apply ended in "Failed to claim interface". The panels that actually own a monitor are asked now, and afterwards exactly those are started again. Everest 60 and Makalu had the same arrangement and the same problem.
- **Six more per-key templates for the Everest Max.** Two by function: **Gaming** lights WASD and what the left hand reaches around it and takes everything else down, **Vim** puts hjkl forward with Esc marked and the rest quiet. Four by shape: **Spectrum** one hue per row, **Diagonal** two colours meeting along a diagonal, **Halo** a warm core over the typing area falling into the dark, **Split** left hand cool and right hand warm. They are computed from the key layout the editor draws, so they follow the keys rather than a list of numbers, and the numpad is deliberately left out of the two functional ones.

## [3.0.2] - 2026-08-03

Two things that made the application look broken when it was not. Source-overlay patch.

- **Only one instance per session.** Starting BaseCamp while it is already running used to start a second full application, and the two then fought over the same USB devices: `[Errno 16] Resource busy`, or the interface lost outright, and the DisplayPad sat on "Connecting to DisplayPad" for good. Nothing about the launcher tells you the application is already there when it is minimised to the tray, so clicking it again is easy to do by accident, and three instances is enough to make the pad look dead. A second start now brings the existing window to the front and exits. It decides by pinging the running instance, so an older one that does not know the new command is still recognised.
- **The device list stopped flickering.** The USB scan runs every five seconds and the sidebar was taken apart and rebuilt on every one of them, whether anything had changed or not: over five scans of an unchanged desk that is 21 removals and 21 re-additions of the device entries. It is now only rebuilt when the list of connected devices really changes. The state dot, the selection and the labels also stopped repainting themselves when nothing about them moved.

## [3.0.1] - 2026-08-03

Follow-ups on the 3.0 reports from @FransM, and two findings from @rebell218's environment. Source-overlay patch, so it arrives through Settings without a new AppImage.

### DisplayPad

- **A page's own settings sit under its keys (#71).** The page name, Rename, Delete, and the auto-timeout with its target were only reachable through the button-actions window, which is about keys, not pages. They are a row under the twelve keys now, aligned with them, and both places share one widget so they cannot drift apart in what they store.
- **The GIF speed box is back (#73).** Minimum milliseconds per frame was on the DisplayPad screen until the 3.0 rebuild and afterwards survived only in the multi-upload window. It sits in the page-settings row now, and the value is kept across restarts, which it never was before.
- **A plugin frame no longer lands on the page you just switched to (#69, #70).** Plugins render in their own threads and their images are uploaded from a queue, so a frame composed for the old page could be written after the new page's icons were already on the device: two keys kept the widget's picture and looked like they had not been refreshed. Whether it happened depended on the moment the switch fell, which is why it was not reproducible. Frames now carry the page they were drawn for and stale ones are dropped. A page switch and a plugin's own thread cannot be ordered against each other, so a frame can still be handed over a moment after the page has changed and would carry the new page's number: a frame for a key that the live page has given to something else, a plain image or another action, is therefore refused as well. A plugin only ever paints keys its own action type sits on, which is how all shipped plugins find their keys, so this does not take anything away from them.
- **The button-actions window no longer resizes itself while you watch (#68).** It appeared at its default size, filled with twelve key cards, and only then jumped to the size you left it at. It is now laid out before it is shown. Opening it also no longer overwrites the size you chose: on a screen too small for that size the window is shrunk to fit right after it appears, and that shrunk size was being saved as your preference, so it ratcheted down a little every time you opened it.

### Everything else

- **A device you may not open says so (#49).** With the udev rule missing or not applied, the device still enumerates, so it appeared in the list as connected while every action quietly did nothing, which is indistinguishable from the app being broken. It now reads "no access" beside the name, the screen names the `/dev` entries that were refused and the command that fixes it, and the notice disappears by itself once the permissions are right, without a restart.
- **One more variable of our own service reached launched programs (#49).** `MEMORY_PRESSURE_WATCH` was stripped but its other half `MEMORY_PRESSURE_WRITE` was not, so a program started from a key still received our cgroup pressure thresholds. Checked end to end now by reading a launched program's own `/proc/<pid>/environ`: nothing of the AppImage, of PyInstaller, or of our unit survives, and nothing points into the mount any more.
- **No "no keyboard" flash on a desk without a keyboard (#67).** The first screen was hardcoded to the keyboard and opened a moment after the startup device scan had already picked the right one, so a pad-only setup saw its pad, then "no keyboard", then its pad again. The scan decides now, and only the screen you land on is built.
- **A tray icon that cannot start no longer takes the app with it (#77).** A build that names an interpreter it did not ship, which is what a Nuitka standalone build does, ended in `FileNotFoundError` out of the constructor and no window at all. The tray is a convenience: it says why it is missing and the app runs.
- **`requires` in a plugin manifest is read the way pip writes it (#76).** `Pillow` imports as `PIL` and `opencv-python` as `cv2`, so checking the manifest string directly reported Pillow as missing on every machine, including the AppImage that ships it, and put a warning on plugins that were working.
- **A plugin's `icon.png` is shown again (#76).** The loader survived the 3.0 rebuild but nothing called it, so plugins that ship an icon, as the plugin guide tells them to, showed none.
- **The plugin detail names the folder it read (#75).** Nothing overwrites an installed plugin unless its version goes up, so a manifest edited in place keeps showing the old author. The pane now says which copy it is describing.

### Documentation

- **PLUGINS.md moved to `docs/` (#72)**, beside CONTROL_INTERFACE.md.
- **The plugin guide matches the code again (#76):** image pushes go through one long-lived worker rather than a thread per push, `ctx.schedule()` is for widgets and never needed for pushing a key image, and the bundled-package list says what a source install adds. Its LED API server example now also exists as a folder you can copy, with its client script, under `docs/examples`.
- **README (#74):** a current DisplayPad screenshot, the settings cog replaced by the sidebar entry that exists, and the page description brought in line with the tabs and the new settings row.

## [3.0.0] - 2026-08-02

The interface has been rebuilt. Same devices, same features, a different application to look at and to move around in. Delivered as a source-overlay patch, so the update arrives through Settings without a new AppImage.

### The interface

- **A sidebar instead of two rows of coloured pills.** Devices are listed down the left with a small dot for their state, and **only devices that are actually plugged in appear**. Below them sit the tools that are always there: macros, plugins, OBS, plugins that bring their own screen. Every screen carries a header strip with the device name, its state, and that screen's actions, so the same things live in the same place everywhere.
- **Settings, plugins, macros and the colour editors are screens now, not windows.** Nineteen separate windows in five different styles have become screens inside the one window, with one shared dialog for the few questions that genuinely need to interrupt: confirm, ask for a name, report an error.
- **The DisplayPad is a single screen.** The pad, its pages, and the editor for the selected key used to be three windows you had to keep straight. It is now the twelve keys as they sit on the device, page tabs above, and an inspector for the selected key beside them.
- **Settings arranged in cards** for updates, profile, application and backup, with the update card offering the update when there is one instead of hiding it behind a check.
- **Plugins as list and detail.** The description, what a plugin needs, and its update state have room now instead of a line of truncated text.
- **Macros show where they are used.** The editor sits beside the list, and each macro names the keys it is bound to.
- **The window can be resized** and comes back the size you left it. It was fixed at 480x760 before.
- **Results say so.** Applying, saving and uploading raise a short message instead of leaving you to guess whether anything happened.
- **The tray menu can switch DisplayPad pages** (companion to the `dp_page` command from 2.1.8).
- **No pictograms in the interface.** Emoji rendered in whatever font happened to be installed, in their own colours, at their own line height, and could not be translated. Where a mark carries meaning it is drawn now, so it takes the text colour and is the same size everywhere.

### Under it

- **A design system.** One file holds every colour, type size and spacing step; a second builds the components from them. A button is one call rather than eight keyword arguments repeated across the code, and a colour has a meaning: accent means you can click it, green is state and never a button fill, red is destructive and outlined only.
- **Screens are built when they are first opened.** Start is **1362 ms instead of 4121 ms**. Nearly all of the old figure was CustomTkinter drawing some 1400 rounded rectangles and 4000 anti-aliased circles for screens nobody had asked for yet. The monitor bars now poll only while their screen is visible.

### Fixes

- **The overlay could not update the language files or the presets (this affects 2.1.x today).** A frozen build looked them up inside the AppImage bundle, which an update never touches, so the files the patch shipped were never read. The bundled language files hold 373 keys; 2.1.1 through 2.1.8 added 30 more, and every one of them showed up in the interface as its own name instead of as text. The DisplayPad page dialogs from 2.1.7 are where you would have met them: `dp_new_page`, `dp_page_name_prompt`, `dp_delete_page_confirm`. Since the fix travels inside the overlay itself, this update repairs it on existing installations.
- **A dropdown could stay painted over other applications (#66), and is only partly curable.** The popup holds a global grab, and under Wayland the compositor activates the other application without this process being told anything at all: no focus event, no pointer event, nothing to react to. Where the system does report it, an X11 session, the popup closes now. Getting rid of it entirely means drawing the popup inside our own window, which is a change of widget and not of this release.
- **A page file with an unusable image entry is read anyway** instead of refused, with a warning naming the key. An image that cannot be read no longer takes the whole upload down with it either: that key is skipped and named on the console, the other eleven still reach the pad, instead of the page failing with the imaging library's own wording.
- **Capitals lost their umlauts in German.** GERÄTE read as GERATE, Über as Uber, on labels and on buttons alike, in every version so far. The interface asked for Helvetica, which is an alias; where it resolves to Nimbus Sans the dots on capital umlauts sit above that font's own ascent, and CustomTkinter sizes its text to exactly that height, so they were cut off. The interface now names Liberation Sans, which measures identically, average and worst case both 100.0% against our German strings, and has the room.
- **Changing the language left part of the interface behind.** Settings kept every one of its labels in the old language, and the sidebar, the screen title and the state pill kept theirs on every screen. Counted against the same walk of the interface: 13 visible labels stayed behind in 2.1.8, and after this pass 7 do, most of which are words that read the same in both languages.

## [2.1.8] - 2026-08-02

Source-overlay patch: the control socket can put the DisplayPad on a page, and a documentation pass.

### Features / Improvements

- **Switch DisplayPad pages from a script (#63, @FransM).** The socket's `page` command only ever switched the GUI tab, so nothing outside the app could change which twelve keys the pad shows. The new `dp_page` command does that: `basecamp --ctl '{"cmd":"dp_page","page":"Editor"}'`, addressing pages by the name you gave them, or by id, or `"prev"` for the page you came from. Re-sending the page the pad is already on reports `changed: false` instead of failing, an unknown name comes back with the list of names that do exist, and the switch goes through the same path as a page action on a key, so a running upload or animation finishes first. `list` now also reports the pad's pages and the page it is on, so a script can discover the names. This makes an editor wrapper that flips to a page of snippets while it runs a three-line shell script, see [docs/CONTROL_INTERFACE.md](docs/CONTROL_INTERFACE.md).

### Fixes

- **Page cache could be read while it was being invalidated.** Plugin threads (`get_displaypad_current_page()` and friends) and now the control socket read the page files off the GUI thread, where the cached lookup checked the cache and copied it in two steps, so an invalidation landing in between raised a `TypeError`. It binds the cache once now. Rare, but the window widened with every off-thread reader.

### Documentation

- **README brought up to date (#51, #63, @FransM).** The page-model rewrite in 2.1.7 covered the DisplayPad section only. This pass adds the plugin index (installing published plugins from the app, manual install from a URL or folder, the source-install caveat for plugins with third-party dependencies), the DisplayPad brightness and debounce controls, the command-line flags, the optional source dependencies, and the Page navigation and Redefine key action types that D1-D4 and K1-K12 have had since 2.1.0 but that only the DisplayPad section mentioned.
- **The control interface document no longer describes itself as untested.** It carried a status banner and an unchecked hardware checklist from when the feature was written.

## [2.1.7] - 2026-08-01

Source-overlay patch built around @FransM's combined contribution (PR #62), which reworks how DisplayPad pages are stored and referenced, rewrites the pad's init sequence, and merges the key listener into the upload worker. Plus a launch-environment fix for applications started from a key.

### Fixes

- **DisplayPad did not initialise reliably on start or replug (#43, #44, @FransM).** The init handshake now claims interface 0 briefly for SET_IDLE on all three interfaces plus the SET_REPORT that enables event reporting, exactly as the Windows capture shows, and it accepts the pad's reply by matching the echo rather than a single byte. @FransM confirmed both the startup timeout and the replug re-init on hardware.
- **Sub-page keys did not match what was on screen, and actions did not fire (#52, #54, @FransM).** Pages are stored one file per page now, and every reference to a page (button action, "also on press" step, double-click, timeout target) is by page name instead of a positional id, so a page keeps its identity as other pages are created and removed. Clearing or setting an image on a sub-page no longer writes into the main page's images.
- **Page switching stopped working while a plugin was pushing (#54, @FransM).** A plugin pushing more often than the worker's idle gap held the device indefinitely and every page-switch retry failed. A pending switch now makes the worker yield immediately, and the flag is cleared only once the page's own upload is done.
- **Plugins kept painting keys of a page that was no longer shown (#54, @FransM).** A service plugin bound to a button is started and stopped with its page through its normal `start()`/`stop()` lifecycle. Plugins with no button binding keep running for the whole session as before.
- **Page dropdown showed duplicate names and pages that vanished (#50, @FransM).** Page names are unique (a duplicate gets " (2)" appended), a page created but not yet targeted by any button is kept instead of being garbage collected, and a deleted page can no longer be resurrected by a deferred page switch.
- **Applications launched from a key behaved differently than from the desktop launcher (#49, @rebell218).** They inherited BaseCamp's own environment: the AppImage identity vars, the PyInstaller bookkeeping vars, and the systemd unit vars of our autostart service, which made a launched app log into our journal stream and watch our cgroup. All of these are stripped now, and shell/app actions run in their own `app.slice` scope via `systemd-run --user --scope` when a user systemd manager is available, so quitting BaseCamp no longer takes them with it.
- **README described the old page model (#51, @FransM).** Rewritten for named pages, unrestricted navigation, page delete/rename and the per-page timeout.
- **One wheel notch scrolled a dialog to the very end.** Tk 9 delivers X11 wheel input as `<MouseWheel>` with a delta of 120 where Tk 8.6 sent Button-4/5 with a delta of 0, and CustomTkinter passes that delta through as a unit count. The panels already capped their scroll step, the dialogs did not, so the button-action editor and the picker lists jumped straight to the top or bottom. Every scrollable area caps its step now, plus one central normalisation so a newly added one cannot regress.

### Features / Improvements

- **Page management in the UI (#50, #52, @FransM).** Create, rename and delete pages straight from the page dropdown in both the image dialog and the action editor. Deleting warns first and lists every button and timeout still pointing at that page.
- **"Also on press" and double-click can pick a page from a dropdown (#48, @FransM).** Both rows now show the same page picker as the primary action instead of a free-text field, and the three rows of a key card line up in one column.
- **The action editor window is resizable and remembers its size (#48, @FransM).**
- **Faster startup and uploads (#57, @FransM).** Image conversion is vectorised, the key listener and the plugin upload worker are one thread holding one device session instead of two that constantly traded the device back and forth, and the page files are cached in memory.
- **F13-F24 available for Keypress actions and macros (@FransM).** Useful for shortcuts that must not collide with a key that physically exists.
- **Malformed config files are reported.** A JSON file that fails to parse prints a warning naming the file instead of silently falling back to defaults.

### Plugins

- **DisplayPad Pipe Text 1.1 (basecamp-plugins #12, @FransM).** Renders text written to a named pipe onto a key, with per-line colour, size, boldness and alignment, plus an optional producer command started with the pipe.
- **DisplayPad Video 1.0 (basecamp-plugins #11, @FransM).** Plays a video file on a key, started by writing its path to a named pipe. Needs `opencv-python` and therefore a source install.

## [2.1.6] - 2026-07-09

Source-overlay patch: a one-fix follow-up on the Everest 60 ESC key.

### Fixes

- **Everest 60 ESC key ignored the custom colour (#46 / followup #33, @FransM).** @FransM confirmed on the hardware that the ESC LED is firmware index 0. The custom map wrote it correctly, but the final packet was zero-padded, and a zero entry is itself an index-0 write of black that overwrote ESC right after it was set (the real reason it read dark in #15, not a missing LED as the old note guessed). The map now pads with a real entry instead of zeros, and ESC is set to index 0, so it takes the colour like every other key. The earlier stopgap (index 21) drove a phantom LED and is gone.

## [2.1.5] - 2026-07-09

Source-overlay patch: follow-ups on the 2.1.4 DisplayPad startup work, two new DisplayPad conveniences, and plugin fixes, all from @FransM's testing.

### Fixes

- **DisplayPad "upload failed: Connection timed out" at startup (#43, @FransM).** A plugin (Clock, System Monitor) could push its first image before the pad had finished booting (its mountain logo not shown yet), and streaming pixels to a not-yet-ready pad timed out. Plugin uploads now wait until the pad has been INIT'd on the current connection, and a short settle follows the INIT handshake before the first image is sent.
- **DisplayPad did not re-init after unplug/replug (#44, @FransM).** A quick unplug and replug re-enumerated the pad under a new device path without the presence poll ever seeing the gap, so no re-init ran (no logo, no key images). The monitor now also reconnects when the pad's path changes, a reconnect that lands mid-upload is retried instead of dropped, and the pad-ready state is reset on every (re)connect.
- **System Monitor: GPU temperature wrong or missing on hybrid laptops (basecamp-plugins #10, @FransM).** On a laptop with an integrated plus a discrete NVIDIA card, the psutil sensor path reported the built-in GPU or nothing. The plugin now reads the discrete card via `nvidia-smi` first and falls back to psutil (amdgpu/nouveau/i915) when it isn't present.
- **Disk (cycle) action still showed a filesystem dropdown (basecamp-plugins #9, @FransM).** Switching a button from "Monitor: Disk" to "Monitor: Disks (cycle)" left the old mountpoint dropdown and its value in place. Changing an action type now rebuilds the value widget for the new type and clears the carried-over value.

### Features / Improvements

- **DisplayPad double-click actions (#47, @FransM).** Each key can carry a second action fired on a quick double press. When set, the primary is held until the click window elapses so the double can win; keys with no double action stay instant.
- **DisplayPad per-page auto-timeout (#45, @FransM).** A page can jump to a target page after N seconds ("After") or after N seconds of no keypress ("Idle"), configured per page in the action editor. The target can be a specific page or the page you came from. Handy for a monitor page that shows all mounts and then returns on its own.
- **Everest 60 ESC-index diagnostic (#46, @FransM).** Filling the keyboard red still left ESC the wrong colour because its firmware LED address was never confirmed. A new `rgb esc-scan` command walks candidate indices, lighting one at a time red on a dim-blue board, so the index that actually lights ESC can be identified on the hardware.

## [2.1.4] - 2026-05-30

Source-overlay patch: a big round of Everest 60 RGB work driven by @FransM's Windows packet captures, a DisplayPad page-model redesign, and several connection/startup fixes.

### Fixes

- **Everest 60 effect panel still hid too many controls (#32, @FransM).** Breathing showed no speed slider, Wave no speed or direction, and switching to a "… Rainbow" entry "stuck". The old show/hide logic relied on widget-mapped state and re-packed rows out of order, so controls fell outside the accordion's fixed height. Effects now declare which colour modes they support and a single **Color-mode** dropdown (Single / Dual / Rainbow) replaces the separate "… Rainbow" entries; the panel re-packs every control in a fixed order and re-measures so nothing is clipped. A version line now sits at the bottom of the form.
- **Everest 60 custom colour flashed the keyboard white / left ESC out (#33, @FransM).** Comparing FransM's Windows capture showed the custom-mode path was sending a mode-detail packet that defaults every key to white before the colour map lands. Windows sends no such packet in custom mode. It's gone now, and the commit/latch packet Windows sends after the map (which we were missing) is sent too.
- **RGB settings not applied on startup/autostart (#42, @solimar1963).** Saved lighting was written to `rgb_settings.json` but never pushed, so the keyboard kept its default lighting until the user pressed Apply. It's now re-applied automatically when the keyboard is detected.
- **DisplayPad stale plugin icon with no action (#41, @FransM).** A key whose plugin action was removed kept showing the old icon (the pad holds the last frame in its own memory, even across reboots). Connecting now always refreshes the pad, and an empty layout still clears every key.
- **DisplayPad connection failed after unplug/replug + restart (#40, @FransM).** Following FransM's Windows-vs-Linux capture comparison: the display interface is now quiesced with the `SET_IDLE` request Windows sends (which Linux was only doing for two of the three interfaces), and the INIT handshake re-sends several times instead of writing once and blocking, so a replugged pad comes back reliably.

### Features / Improvements

- **Everest 60: Matrix effect (#38, @FransM).** Added from FransM's capture. It's a dual-colour firmware effect with speed and brightness.
- **Everest 60: full Breathing colour modes (#39, @FransM).** Breathing now offers Single, Dual and Rainbow with speed and brightness, matching the hardware.
- **DisplayPad page-model redesign: carousels & chain-to-page (#30 / #17, @FransM).** Pages are no longer tied to a main-page button slot: any key on any page can switch to any page, so a true carousel (A→B→C→D→A on one key, reverse on another) is now expressible. A page's back button is a normal editable key (change its icon/target or remove it), a "page" button can use a custom icon, and "also on press" can jump to a page (e.g. press the CPU key → also open a per-core page). Existing multi-page setups are migrated automatically.
- **Everest 60 side ring: per-LED painting + lit default (#4, @FransM).** The Custom RGB editor now shows the 44 side-ring LEDs as a paintable per-LED strip below the keyboard (they map to ring hardware indices 126 to 169) and saves them with the rest of the per-key layout, so each ring LED can be set individually. Separately, when there's no saved per-key state to preserve, the side-ring quick-picker lights the keys white instead of blanking them so the keyboard never goes dark under low light. (The strip is a plain per-LED editor, not a physical ring map; the numpad-row indices still need a capture from numpad hardware.)

## [2.1.3] - 2026-05-30

Source-overlay patch: a round of DisplayPad fixes plus two usability additions, all from @FransM's testing.

### Fixes

- **DisplayPad "upload failed: [Errno 16] Resource busy" (#26, @FransM).** A different race from the #23 timeout: the manual image-upload path grabbed the USB interface immediately instead of waiting for the key-event listener to let go first (only the plugin upload did that). Opening the hidraw node while the listener still held it returned *Resource busy*, and the unsynchronised `_uploading`/`_animating` flags let two upload sessions overlap. A single device lock now serialises every USB session, both paths wait for the listener to step aside, and the open retries on a transient busy.
- **DisplayPad key presses sometimes ignored (#27, @FransM).** With a live plugin pushing about once a second, the key-event listener was paused for each upload and could take up to half a second to re-attach afterwards, leaving a blind window where presses were dropped. The re-attach is now prompt, so plugin/clock keys respond reliably.
- **New DisplayPad page looked pre-filled (#28, @FransM).** Adding a page and opening it sometimes showed the main page's images. Switching page was silently aborted while a plugin upload held the device (which is almost always, with a live monitor), so the editor moved to the new page while the panel and device stayed on the old one. The switch now waits for the upload to finish instead of dropping.
- **"Redefine key" action missing from the dropdown (#29, @FransM).** The `set_key` action type shipped in 2.1.0 but was never listed in the action editor. It's now selectable, with a JSON hint for the target.

### Improvements

- **Auto-generated key icons for keypress/text actions (#31, @FransM).** A key set to a keypress or text action with no image of its own now gets a generated label icon so it isn't blank. A user-assigned image is never overwritten.
- **Language picker in Settings (#35, @FransM).** The language selector now also lives in the ⚙ settings dialog, so it can be changed even when only a DisplayPad (no keyboard panel) is connected.
- **Everest 60: "Custom" is now an effect (#34, @FransM).** The separate Custom RGB section is gone; "Custom" is an entry in the effect dropdown that opens the per-key editor, so the dropdown reflects the actual mode instead of showing a stale effect name.
- **DisplayPad usbhid quirk diagnosed and documented (#36, @FransM).** When the DisplayPad is on the USB bus but its command interface never enumerates (the Ubuntu/Mint interface-order quirk), the app now prints a clear hint and shows it in the panel instead of silently reporting "not connected". The README documents the `usbhid quirks=0x3282:0x0009:0x4000` fix.

## [2.1.2] - 2026-05-26

Source-overlay patch with two issue fixes, both reported by @FransM.

### Improvements

- **"Also on press" action on DisplayPad keys (#16, @FransM).** Every key in the action editor now has an optional second action (keypress, text, shell or url) that fires on press in addition to its main type. This is what lets a live System Monitor key (CPU, RAM, ...) also send, for example, F12 via ydotool, which was exactly the use case in the issue. So a monitor or plugin key that only draws a widget is no longer a dead key. The action chain was already executed under the hood since 2.1.0, but there was no way to set it from the GUI until now.
- **Editing a key no longer drops its secondary action.** Saving a key's primary action used to silently discard any attached action chain. It is preserved now.

### Fixes

- **Everest 60 `side-static` no longer blanks the keyboard (#4, @FransM).** Setting the side ring through the control interface or the controller CLI (`--ctl '{"cmd":"rgb","device":"everest60","args":["side-static",...]}'`) lit the ring but turned the main keys off (ESC went white, the rest dark). The side ring can only be driven in custom mode, which also carries a full per-key colour map, and the command was sending an all-black map. It now loads your last saved per-key colours and sends them alongside the ring, the same way the GUI side picker already does.

## [2.1.1] - 2026-05-26

The first source-overlay patch on top of 2.1.0, so it ships as a small tarball that the in-app updater installs in a couple of seconds instead of a full AppImage download. Four issue fixes, all reported by @FransM.

### Fixes

- **Configure button actions on the DisplayPad (#24).** When one key was set to a System Monitor action (CPU, RAM, disk, ...), the action dropdowns of all the keys below it stopped showing the action types and listed the mounted filesystems instead. A loop variable that held the disk options was reusing the same name as the list of action-type labels, so it overwrote the labels for every following key. The two are now kept apart and every key shows its real action again.
- **DisplayPad "upload failed: Connection timed out" repeating forever (#23).** With a live plugin running (System Monitor, Clock, ...) the device could get stuck printing that line on every update. The plugin image upload was opening its own connection while the key-event listener still held the device, and the two fought over the same USB interface. Plugin uploads now wait for the key listener to step aside, take the device for one batched upload (keeping only the newest image per key), and hand it back, the same way a normal upload already did. Key presses that arrive during the upload are still delivered, and a genuine device error is now logged once rather than on every tick.
- **Backup file picker opened in /usr/share/icons (#25).** The picker reused the icon-browser default. Backup and restore now open in your home directory.
- **Startup tab follows the first connected device (#22).** Launching with no keyboard connected but, say, a DisplayPad plugged in used to land on the empty Keyboards page. It now opens the tab of the first device that is actually connected. With nothing connected it stays on Keyboards so the empty state can explain how to connect.

## [2.1.0] - 2026-05-24

A big round of issue fixes plus three new capabilities: an external control interface, multi-action keys, and a live-update system that now reaches the whole app instead of just the GUI. This is a full AppImage release (both Debian and Fedora variants); from 2.1.1 onwards, pure-Python patches can once again ship as tiny source-overlay tarballs, now across every part of the app and every distro at once.

### Fixes

- **Everest Max button actions fire again on AppImage installs (#11, reported by @djibux).** App and Shell actions silently did nothing under Wayland/GNOME while URL actions worked. The cause was the AppImage's bundled library paths (`LD_LIBRARY_PATH`, `APPDIR`, …) leaking into every launched program, so GUI apps loaded our bundled libraries and failed to start before they ever appeared. Launched programs now get a sanitised environment. The same fix repairs the icon file picker not opening on GNOME (zenity/kdialog were hitting the same contamination). Firmware-level key remaps configured in Windows BaseCamp still cannot be cleared from Linux: the Reset button now says so explicitly instead of implying it did.
- **Tray icon no longer dies when the notification area restarts (#21, @FransM).** pystray's X11 backend raised "Failed to dock icon" and killed the tray thread for good after a desktop-panel restart or idle. The tray now supervises itself and simply re-docks.
- **"No keyboard detected" screen (#19, @FransM).** Launching with no Mountain device connected showed a panel full of inert controls; it now shows a clear empty state and recovers automatically when a device is plugged in. Software tabs (OBS, Macros, Plugins) stay reachable.
- **Everest 60 ESC key now lights up (#15, @FransM).** It was mapped to LED address 0, which has no physical LED (and a zero index is indistinguishable from the zero padding at the tail of each packet) so it stayed dark on a full-keyboard fill. Corrected to index 21.
- **Everest 60 side LEDs keep the main keys (#4, @FransM).** Applying a side-ring colour no longer blanks the per-key lighting; the last saved key colours are pushed alongside the ring in a single write.
- **Python 3.14 crash on the plugin error path.** Two deferred callbacks referenced an `except … as e` variable after it had gone out of scope, raising `NameError` instead of showing the error. Bound the variable so the error display works.

### Control interface (#20)

While the app runs it hosts a small local Unix socket so external programs (a calendar reminder, a mail hook, a CI script) can drive the hardware: set colours, switch pages, push an image to a DisplayPad key, or redefine a key. One JSON object in, one JSON reply out, from any language:

    basecamp --ctl '{"cmd":"rgb","device":"everest60","args":["side-static","255","0","0"]}'

The full command list is in `docs/CONTROL_INTERFACE.md`.

### Action chains and new action types

- **Multiple actions per key press (#17, @FransM).** A key can run several actions in sequence, for example launch a command and then switch to a page.
- **Redefine-key action (#18, @FransM).** A key press can reassign another key, for modal / layered layouts.
- **Switch-page action and actions on plugin keys (#16, @FransM).** Any key can jump to another tab, and plugin/monitor keys can carry a press action so they are no longer dead keys. (Mapping the Everest 60's own keys to F1–F12 still needs the firmware remap protocol, which is not reverse-engineered yet.)

### Live updates now cover the whole app

Until now the source-overlay updater only patched the GUI process; the button-action daemon, the tray, and the per-device controllers ran as separate frozen binaries the overlay could not reach, so any fix in them required a full AppImage. Each of those binaries now uses the same tiny entry-shim + overlay pattern as the GUI (`emax_controller.py` and friends are imported by stable entry shims), so a pure-Python fix anywhere in the app can ship as the small tarball. This takes effect once everyone is on 2.1.0, the first build that carries the shim binaries; from 2.1.1 on, most patches will be source-only again, and one tarball still serves both the Debian and Fedora builds.

### Build

A reproducible Debian build (`Dockerfile.debian` + `build_appimage_debian.sh`) on `python:3.14-bookworm` produces the `-debian` AppImage against Debian's own glibc, so it runs on Debian 12 / Ubuntu 22.04 / Mint and newer (tested on Mint 22.3). The previous `-debian` image had been built on the Fedora host and actually required a glibc too new for Debian 12, so this is the first genuinely Debian-compatible build. The `libusb` lookup in the controller spec is now distro-agnostic.

## [2.0.3] - 2026-05-15

Decouples the in-app update check from GitHub's "Latest" pin. The previous code queried `/releases/latest`, which meant whichever release was flagged as Latest on GitHub had to be the newest one to keep auto-updates working. The new code scans the recent release feed instead and picks the highest version number, skipping prereleases and drafts. This lets the project keep v2.0 (which carries the AppImage assets that new users download) pinned as Latest indefinitely, while small source-only patches (2.0.x) still surface in the app for everyone running it. From this version onwards, the Latest pin on GitHub is purely a landing-page hint for new users and has no effect on the updater.

## [2.0.2] - 2026-05-15

Tiny follow-up on 2.0.1. The update popup was only firing when a release shipped an AppImage asset, because the trigger check was looking at the AppImage URL variable instead of the resolved update URL. Source-only releases (which is what 2.0.1 itself was) set the resolved URL via the source tarball, so the green up-arrow on the settings cog appeared, but the proactive popup did not. Fixed by gating the popup on the resolved URL.

If you noticed the green cog after 2.0.1 dropped but no popup ever appeared, this is exactly the bug. Open the settings dialog manually and click "Jetzt aktualisieren" to grab this patch; from 2.0.2 onwards every future source-only release will pop up like the original AppImage-driven one did.

## [2.0.1] - 2026-05-15

First real source-overlay patch on top of 2.0, ships as a 200 KB tarball that the in-app updater installs in a couple of seconds. Touches one thing only:

- **Bundled plugins now refresh correctly when shipped through a source overlay.** The function that copies bundled plugins (now_playing, ...) into `~/.config/mountain-time-sync/plugins/` was resolving its source directory from PyInstaller's `_MEIPASS`, which always points at the AppImage's bundled location and silently skipped any updated plugin a source-overlay tarball was carrying. It now resolves from its own module path instead, so an overlay that ships a newer `plugins/now_playing/` is picked up like every other Python file in the overlay. User-installed third-party plugins are unaffected; they have always loaded from the user config directory and have nothing to do with this code path.

This is also the first end-to-end test of the source-update pipeline introduced in 2.0. If you are on 2.0 already, you should see the update popup on next startup and the whole thing should take about as long as it takes to read this paragraph.

## [2.0] - 2026-05-15

This release brings a proper in-app updater so you finally do not have to download a 250 MB AppImage every time something small needs to change. Underneath sits a source-overlay system that lets pure Python patches ship as tiny tarballs, typically around 200 KB instead of 250 MB, so updates between major releases happen in seconds instead of minutes. The popup that asks you whether to update is new too, and the settings cog in the header turns green with a small up-arrow the moment a new version is detected, so you actually notice that there is something to do.

### In-app updater

When the app starts it quietly asks GitHub if there is a newer release. If there is, a popup appears with two buttons (Update now or Later) so you can decide right away. The settings cog in the top-right corner also turns green with a "⚙ ↑" indicator that stays visible until you update or restart, so the hint is always there if you closed the popup.

Clicking the update button downloads the new version in the background with live progress, swaps it into place, and offers a Restart button. The restart re-launches the app via execv and stops the tray helper first so you do not end up with two tray icons.

All popup labels are translated, so users running the app in German will see "Update verfügbar / Jetzt aktualisieren / Später" instead.

### Two update paths, chosen automatically

The updater picks between two flavours behind the scenes:

- **Source overlay** is the small path used for most updates. When a release ships a `source-X.Y.Z.tar.gz` asset, the app downloads it (around 200 KB), unpacks it into `~/.local/share/basecamp-linux/source-overlay/`, and on the next start PyInstaller's runtime hook spots the overlay and prepends it to `sys.path`. All Python code then resolves to the overlay files instead of the bundled copies inside the AppImage. The AppImage itself is never touched, which means Debian and Fedora users get the exact same patch from the exact same tarball.
- **Full AppImage swap** is the bigger path, used when native dependencies change. The updater downloads the right AppImage variant for your distribution and atomically replaces the running file. Variant picking now reads `/etc/os-release`: Debian, Ubuntu and Mint get the debian build, everything else (Fedora, Nobara, Arch, Manjaro, openSUSE and friends) gets the fedora build, since rolling-release distributions handle the newer glibc without issues.

### Tamper protection for source updates

Source tarballs must come with a matching `source-X.Y.Z.tar.gz.sha256` sidecar on the GitHub release. The updater fetches the checksum before it even starts the download, computes SHA-256 of the bytes as they come in, and aborts with a clear error if the result does not match. A tarball published without a checksum is treated as suspect and the app silently falls back to the full AppImage path.

Extraction uses Python's `tarfile.data_filter`, which refuses path-traversal entries, absolute paths, symlinks pointing outside the destination, device nodes, named pipes and setuid bits. Even a compromised release pipeline cannot drop files outside the overlay directory or smuggle in a setuid binary.

### What this means for you as a user

For most patches the new flow is simply: see the popup, click "Update now", wait a couple of seconds, click "Restart now". No browser, no manual download, no chmod +x.

If you installed via AUR (`basecamp-linux` via yay) or from source, the popup does not appear since those workflows have their own update mechanism. You still get the green cog and the version line in settings, which points you to the right command for your install.

### Plugin compatibility

Nothing in the plugin API changed. Plugins continue to live in `~/.config/mountain-time-sync/plugins/` and are loaded exactly as before. The source overlay only contains bundled plugins from this repo, never user-installed ones, so third-party plugins are completely untouched by the update mechanism.

## [1.8.1.2] - 2026-05-15

Source-only patch on top of 1.8.1.1: picks up another round of issue triage with @FransM (#3, #4, #5, #6, #12, #13, #14). Highlights:

- **Bundled plugins now auto-refresh on app upgrade.** The first-run copy step previously only fired if the destination didn't exist, so a fixed plugin in the host repo never reached `~/.config/mountain-time-sync/plugins/`. The app now compares versions and refreshes plugin source files in place (config.json / user state files are left alone). Fixes Frans' point in #13.
- **Plugin update count in the sidebar.** When the manager's background fetch finds newer versions in `basecamp-plugins`, the "Plugins" sidebar button picks up a green "↑N" counter: you no longer have to open the panel to see there's something to update.
- **Copy/paste in button-action fields.** Right-click menu (Cut / Copy / Paste / Select All) plus reliable Ctrl+C/X/V/A bindings on every DisplayPad action entry. Closes #14.
- **DisplayPad paging fixes.** Switching pages in the editor now also flips the live device to that page so the buttons you see in front of you always match the dialog. Setting a sub-page slot to "none" actually blanks the tile on the next upload. Addresses #5.
- **Disk Monitor: pick a mount-point from a dropdown.** New plugin-API hook (`value_options=` on `register_action_type`) lets plugins prefill the button-action editor with a list of suggestions. System Monitor uses it to show all mounted filesystems with size + fstype, so the user no longer has to remember the exact path. Closes #3.
- **System Monitor: CPU temperature caption.** Always shows "CPU" instead of falling back to whatever raw label the sensor exposed ("Package id 0", "Tctl", …). Closes #4 in the plugins repo.
- **Everest 60: unused effect controls now hide instead of grey out.** Rainbow modes no longer show empty Color 1 / Color 2 boxes; Static no longer shows a disabled speed slider. Closes #12.
- **Everest 60: side LEDs (44-LED perimeter ring), initial support.** Reverse-engineered from @FransM's USB capture in #4. New "Side LEDs" panel section lights the whole ring in one colour; new `everest60-controller rgb side-static R G B [bri]` CLI; per-key controller path now accepts a `side` array in its JSON payload. Custom RGB editor integration still to come.

## [1.8.1.1] - 2026-05-14

A small patch release picking up things that came in from issue #2 (thanks @FransM):

- **`ICON_PATH` environment variable**: set it to your own icon library and every first-time file picker starts there. Lookup order is now: last folder you used → `$ICON_PATH` → `/usr/share/icons`.
- **Reset remembered folders**: new button in the settings dialog wipes the per-context "last folder" memory in one click. The next picker falls straight back to `$ICON_PATH` or `/usr/share/icons` again.
- **Autostart on Linux**: added a short README section with the XDG `.desktop` recipe under `~/.config/autostart/`, works on GNOME, KDE, XFCE and friends.
- **Plugin image colors fixed.** `push_plugin_image` was unpacking the channel tuple into variables named `b, g, r` while they actually held R, G, B, so the merge ended up doing nothing instead of swapping red and blue. Every plugin that pushes live images through the API (System Monitor, Now Playing) was rendering with inverted colors. Now it isn't.

No new binary release for this one: pull the source and run, or wait for the next AppImage build.

## [1.8.1] - 2026-05-14

This release is mainly about quality-of-life: a new settings dialog with backup/restore and profiles, a much better experience for everyone on Wayland, and a long list of bug fixes that came out of community reports and a thorough code review. Big thanks to everyone who opened GitHub issues: most of the fixes here exist because of you.

### New: Settings dialog (⚙ button in the header)

There is now a settings cog in the top-right corner that opens a small dialog with three useful things:

- **Backup & Restore.** Export everything (keyboard buttons, DisplayPad pages, OBS config, macros, page names, …) into a single ZIP file. Restore it on the same machine or move it to another one. Your image libraries and plugins stay separate so the backup stays small. Restoring asks for confirmation first, and refuses any ZIP that tries to write outside the config folder.
- **Profiles.** Save your current setup under a name like "Gaming", "Work" or "Streaming" and switch between them later. Each profile snapshots the keyboard actions, the entire DisplayPad layout (images, actions, pages), your OBS connection and your macros. Image libraries stay shared so you don't waste disk space.
- **Update check.** When you open the app it quietly asks GitHub whether there is a newer release, and if so shows a green "↑ v1.8.2 available" line. It also detects how you installed BaseCamp Linux and tells you exactly what to do: download the new AppImage, run `yay -Syu basecamp-linux`, `sudo apt upgrade`, or `git pull`, whichever is right for your install.

### Better file picker

- The app remembers the last folder you picked an image from: every dialog now opens where you were last time instead of dumping you in your home directory every single time.
- If you've never picked anything yet, it starts in `/usr/share/icons` so you can use system icons straight away.

### DisplayPad: Drag & Drop

You can now drag a PNG, JPG, GIF or WebP directly from your file manager onto a button tile in the "Assign Images" dialog. The image gets imported into the library and uploaded to the device, same as if you had clicked the slot and browsed for it.

### DisplayPad: Clear All clears more

"Clear All" used to leave button actions in place, so a button could still trigger a shell command even though its image was gone. Now Clear All also resets the actions to "None". Pages and the "Back" button on sub-pages are preserved so you can still navigate.

### Plugin Manager: spots updates on GitHub

The plugin manager now checks the version of every installed plugin against the central plugin index on GitHub. When a newer version is published you get a green **↑ v1.1** pill on the plugin card (visible even when the card is collapsed) plus an explicit **↑ Update to v1.1** button when you expand it. One click downloads and replaces the plugin. The "Available Plugins" list also shows a green **Update** button instead of the greyed-out "Installed" tag for plugins that have a newer version waiting.

### Fixes from GitHub issues

- **#3: Deleting a DisplayPad image now actually clears the device.** Before, right-clicking a slot removed it from the GUI but the old image stayed visible on the pad until you restarted the app.
- **#5: Page names finally show up everywhere.** If you renamed page 6 to "Stream", it used to keep showing "Page 6" in the dropdowns and on the folder icon. Now your custom name is used consistently: in both dialogs, in the page indicator, and on the folder icon on the device.
- **#6: Apply no longer eats your typed text.** If you typed an action and clicked Apply without first clicking out of the field, your text was lost. Now Apply forces the field to commit before saving.
- **#7 (New "Text" action type.** Map a DisplayPad or D1-D4 button to a string of text) it gets typed out when you press the key. Great for Everest 60 owners who miss F-keys, or for anything you find yourself typing all the time.
- **#10: DisplayPad keypress actions work on Wayland now.** The old code only used `xdotool`, which doesn't work on Wayland. Now the app auto-detects your session and uses `ydotool` instead when needed.

### Bug fixes

- **Switching language no longer crashes the keyboard panel.** Internal naming bug that took out the whole keyboard tab whenever you changed language. Fixed.
- **Hold a button during a GIF? No more spam.** When a fullscreen GIF was animating on the DisplayPad and you held down a key, the action used to fire on every frame. Now it fires once per press, like you'd expect.
- **Switching pages while a re-upload is retrying now works.** If the device was busy and the app was retrying the upload, switching to a different page would re-upload the old page's images. Fixed: the retry now picks up your current page.
- **Clear All on the DisplayPad no longer races with key events.** A race condition could cause a button press to be misinterpreted while Clear All was running. Fixed.
- **Big image uploads can't deadlock anymore.** Long uploads of the keyboard's main display could theoretically lock up if the controller printed enough error text. Replaced with a safer streaming approach.
- **The image dialog closes cleanly when you quit the app.** No more harmless-but-ugly `TclError` traceback on shutdown.
- A handful of smaller fixes around file handles and image-size validation that came out of a thorough code review.

### Security & robustness

- **SUDO_USER is now treated as untrusted input.** The app runs as root for USB access, and previously a poisoned environment variable could redirect root's file writes into another user's home directory. Now the value is validated against the password database and refused if it points at root or a non-existent account.
- **Your config directory belongs to you again.** When the app runs as root via sudo, the config folder is automatically chown'd back to your user so you can still edit files in `~/.config/mountain-time-sync/` without needing sudo.

## [1.8.0] - 2026-04-08

### Plugin System

- **Plugin architecture**: plugins can now extend the app without modifying core files; drop a folder into `~/.config/mountain-time-sync/plugins/` and restart
- **3 plugin types**: Panel (new GUI tab), Action (new button action type for DisplayPad/Everest Max), Service (background daemon thread); a single plugin can be multiple types at once
- **Plugin API (PluginContext)**: stable interface for plugins: i18n, config load/save, GUI scheduling, device access, DisplayPad image push, action registration
- **Auto-discovery**: `PluginManager` scans the plugins directory on startup, loads `plugin.json` manifests, imports and instantiates `Plugin` classes via `importlib`
- **Dynamic action types**: DisplayPad K1-K12 and Everest Max D1-D4 action type dropdowns now include plugin-registered types automatically
- **DisplayPad integration**: plugins can push live 102×102 images to any DisplayPad button via `ctx.push_plugin_image()`, with auto-detection of assigned buttons and GIF animation compatibility
- **Plugin action preview tiles**: DisplayPad panel grid shows blue-bordered tiles with action label text for plugin-assigned buttons
- **Plugin switcher buttons**: panel plugins get their own button in a new row of the device switcher bar
- **Service lifecycle**: service plugins are started after GUI init and stopped cleanly on app shutdown
- **Error isolation**: a failing plugin does not crash the app; errors are logged to console
- **`default_disabled` manifest field**: plugins can opt to start disabled on fresh installs

### Plugin Manager

- **New "Plugins" tab** in the switcher bar: shows all discovered plugins with status (Active / Disabled / Error), version, author, description, and type
- **Enable/Disable**: toggle plugins on or off; disabled state persists in `plugins_disabled.json`
- **Live enable**: enabling a plugin loads it immediately; panel plugins need an app restart to appear in the switcher
- **Active counter**: "2 / 3 active" display with restart hint for panel changes
- **Colored type badges**: "panel", "service", "action" shown as colored pills (blue, green, amber)
- **Accent border**: colored card border: green (active), gray (disabled), red (error)
- **Plugin icons**: optional `icon.png` in the plugin folder is displayed as 28x28 icon in the card
- **Collapsible cards**: plugin cards are compact by default (one line); click to expand for description, author, and error details

### Now Playing Plugin (Example)

- **Bundled example plugin**: shows what's playing in your browser (YouTube, Spotify, etc.) via MPRIS/playerctl
- **Panel**: thumbnail card with title, artist, progress bar, play/pause, mute, volume slider
- **DisplayPad widget**: live 102×102 image with title, artist, status bar, play/pause icon on the assigned button
- **Action type**: "Now Playing" action for DisplayPad/Everest Max buttons: press to toggle play/pause
- **Volume via pactl**: uses PulseAudio/PipeWire sink control (Chrome ignores MPRIS volume)
- **DejaVu Sans font**: full Unicode/Umlaut support across Linux distributions

### Documentation

- **PLUGINS.md**: comprehensive plugin development guide: API reference, DisplayPad integration (auto-detect button, GIF compatibility, preview tiles), UI styling, thread safety, debugging, 4 complete examples

### Everest 60: Protocol overhaul (thanks to [@FransM](https://github.com/FransM) for reverse-engineering and testing!)

- **SetMode (0x16) fix:** buf[5]=0x01, effect code moved to buf[9], sent before SendModeDetails now
- **SendModeDetails (0x17) fix:** Correct byte layout for colors, speed, brightness
- **Response verification:** Echo check now reads resp[1] (was resp[0]); retries up to 3× if device is busy
- **COLOR_RAINBOW = 0x02** (was 0x01), new **COLOR_DUAL = 0x10** for dual-color effects
- **Dual color support:** Breathing, Wave, Reactive, Yeti now use COLOR_DUAL, both colors sent correctly
- **Tornado direction fix:** CW=0x0A, CCW=0x09 with inversion formula; tornado is single-color only
- **Custom RGB: LEDIDX hardware mapping**: byte 4 is now the physical LED address (table by FransM)
- **Custom RGB: packet flag fix**: 0x0E = more packets, 0x0A = last packet (was inverted)
- **Custom RGB: mode activation**: `_send_mode(EFFECT_CUSTOM)` called before uploading per-key colors
- **Custom RGB: byte order fix**: color entries sent as IRGB (index, R, G, B) instead of RGBI
- **Custom RGB: header offset fix**: packet payload starts at byte 9, not byte 6
- **Custom RGB: buffer overflow fix**: `COLORS_PER_PKT` corrected from 56 to 14 (14 × 4 = 56 bytes in 65-byte report)
- **Arrow Up LED index**: corrected from 95 to 99
- **Timing fix:** Added 50ms sleep after `get_feature_report` for device stability

### Everest 60: Layout & presets

- **Removed backtick/tilde key**: does not exist on the Everest 60 (64 keys)
- **Equal row widths**: all rows use proportional spacing, fixing rows 2+3 being shorter
- **Arrow key cluster**: row 4 has small right shift + ↑ + Del, row 5 has ← ↓ →
- **Default presets**: Synthwave, Ocean, Ember, Forest, Arctic, Galaxy (auto-loaded on first use)
- **"Shoreline" preset for Everest Max**: ocean wave gradient from deep navy to bright foam

### Custom RGB Editor

- **QWERTY / QWERTZ layout toggle**: switch keyboard label display between US and German layout
- **Live brightness**: brightness slider sends changes in real-time (300ms debounce, Everest 60 only)
- **Eyedropper shortcut**: changed from Alt+Click to Shift+Click (Alt conflicted with window managers)

### New features

- **DisplayPad Keypress action** (new "Keypress" action type for DisplayPad buttons; simulates keyboard input via `xdotool` (e.g. `grave`, `F12`, `ctrl+shift+a`)) useful for keys missing on compact keyboards like the Everest 60
- **Autostart minimized**: app starts in tray when launched via autostart (`--minimized` flag)
- **`--install` updates autostart**: running `--install` with a new AppImage also updates the autostart .desktop path
- **`--install` refreshes desktop cache**: runs `update-desktop-database` automatically

### i18n

- **Full translation coverage**: all CustomRGBWindow, Everest 60 panel, and color picker strings moved to lang files (~30+ keys)
- **Plugin UI**: all plugin manager labels translated (en + de)
- **Removed 14 duplicate keys** in en.json

### Bug fixes

- **Auto-detection fix**: device detection runs immediately on startup; Everest 60 auto-switches without manual change
- **Crash fix (`_rgb_apply_row`)**: reordered initialization to prevent `AttributeError` on startup
- **Display sleep recovery**: window restore forces full geometry cycle, re-packs active panel, refreshes switcher colors
- **Custom RGB button not updating on language switch**: now registered with `_reg()`
- **Direction not persisted**: RGB direction setting saved and restored on restart
- **Speed slider visual glitch**: slider position refreshes correctly when switching effects
- **Color picker going behind main window**: dialog stays on top with focus
- **SEGFAULT on exit**: HID background threads stopped before window destruction to prevent libusb crash
- **CTk widget rendering on panel switch**: buttons and other CTk widgets appeared broken until hovered; panel switcher now forces `_draw()` on all child widgets after switching, fixing incomplete rendering across all panels

### Security & stability

- **Command injection fix**: replaced `shell=True` with `["bash", "-c", action]` in button action execution (3 files) and macro shell runner
- **Path traversal fix**: mouse recording filenames sanitized with `os.path.basename()`
- **Tray helper path validation**: lang file argument validated to be inside `lang/`
- **Autostart/desktop entry path quoting**: Exec= paths now quoted, fixing paths with spaces
- **File descriptor leaks**: replaced ~20 `json.load(open(...))` with `_read_json()` helper using `with` statements; fixed fd leaks in gui.py, everest_max/panel.py, macros.py, CPU monitor
- **Upload pipe deadlock**: replaced `proc.stdout.read()` + `proc.wait()` with `proc.communicate()`
- **Debounce timer crash**: brightness timer cancelled on window close
- **Everest 60 controller**: `NUM_KEYS` corrected from 191 to 64, tornado direction bounds check
- **Preset consistency**: added missing `brightness: 100` to 6 default presets

### New files

- `shared/plugins.py`: PluginManager (discover, load, shutdown, action registry, enable/disable)
- `shared/plugin_api.py`: PluginContext (i18n, config, GUI, device access, action registration)
- `devices/plugins/panel.py`: Plugin Manager panel (view, enable, disable plugins)
- `PLUGINS.md`: Plugin development guide

### Changed files

- `shared/config.py`: added `PLUGINS_DIR` + `PLUGINS_DISABLED_FILE` path constants
- `gui.py`: PluginManager integration (init, panel registration, switcher buttons, shutdown)
- `devices/displaypad/panel.py`: dynamic action types, plugin action handler fallback, plugin image push, preview tiles
- `devices/everest_max/panel.py`: dynamic action types, plugin action labels
- `devices/everest60/controller.py`: protocol fixes, LEDIDX mapping, NUM_KEYS correction
- `devices/everest60/panel.py`: layout fix, QWERTZ toggle, live brightness, i18n
- `mountain-time-sync.py`: plugin action handler stub, autostart minimized
- `lang/en.json` + `lang/de.json`: plugin UI keys, Custom RGB keys, Everest 60 keys
- `default_presets.json`: Everest 60 presets, Shoreline preset, brightness field

---

## [1.7.0] - 2026-03-29

### Macro System: New Feature

- **New top-level Macros tab** in the switcher bar: create, edit, and manage macros independently from any device
- **Macro Editor**: Named macros with ordered action sequences, repeat modes (Once / N Times / Toggle), duplicate, delete, export/import as JSON
- **Auto-naming**: New macros get unique names automatically (Macro, Macro 1, Macro 2, …)

### Macro Actions

- **Key Down / Key Up / Key Tap**: Keyboard input simulation with a **Rec button**: press Rec, then press any key on your keyboard to capture it
- **Mouse Click**: Left, right, middle, back, forward, with **Rec button** that opens a click-capture dialog (back/forward as quick-pick buttons for side mouse buttons)
- **Mouse Move**: Absolute screen position (x, y)
- **Mouse Path**: Saved mouse movement recordings, record once, reuse in any macro
- **Mouse Scroll**: Up/down with configurable scroll amount
- **Delay**: Configurable wait time in milliseconds
- **Type Text**: Type a string character by character
- **Shell / URL / Folder**: Run commands, open URLs, open folders

### Mouse Recording

- **Rec Mouse** button opens a fullscreen overlay with a screenshot of the desktop as background, so you can see your screen while recording. This is needed because Wayland does not allow apps to track the mouse cursor across the screen; a fullscreen window with a desktop screenshot solves this by receiving mouse motion events while still showing you where you're pointing. The screenshot is taken locally, used only for the overlay background, never sent anywhere, and automatically deleted when recording stops
- **Space to start/stop** recording, no mouse click needed (avoids recording the stop-click position)
- Mouse movement captured via Motion events at ~50ms resolution, works on **X11 and Wayland**
- Recordings saved as reusable JSON files in `~/.config/mountain-time-sync/mouse_recordings/`
- **"Add left click at end"** checkbox (enabled by default), automatically appends a click at the final position
- Recordings manageable: pick from saved recordings via **"..."** button, delete with **✕** in the picker
- Screenshot tools: `spectacle` (KDE), `grim` (Sway), `gnome-screenshot` (GNOME), `scrot` (X11)

### Macro Assignment

- New **"Macro"** action type available on **D1–D4** (Everest Max) and **K1–K12** (DisplayPad)
- Macro picker dropdown shows all saved macros by name
- Macros execute in a background thread when the assigned button is pressed

### Input Tool Support

- **Auto-detection**: Finds `xdotool` (X11) or `ydotool` (Wayland) automatically
- **ydotool key mapping**: Full Linux input-event-codes mapping for all keys
- **Clear error message** if no input tool is installed: shows the install command for Fedora, Debian, and Arch

### Internationalisation

- Full DE/EN support for all Macro features (20+ new translation keys)

---

## [1.6.3-beta] - 2026-03-29

### Mountain Everest 60 Keyboard: Full Support

- **Automatic detection**: Everest 60 ANSI (PID `0x0005`) and ISO (PID `0x0006`) detected automatically on startup, dedicated panel with RGB controls
- **RGB Lighting**: Full effect control (Static, Breathing, Breathing Rainbow, Wave, Wave Rainbow, Tornado, Tornado Rainbow, Reactive, Yeti, Off) with speed, brightness, color pickers and direction
- **Custom RGB Mode**: Per-key color editor with 60% ANSI layout (61 keys), separate config and presets from Everest Max
- **Keyboard switcher label**: Shows "Everest Max" or "Everest 60" depending on which keyboard is detected (like "Makalu 67" / "Makalu Max" for mouse)
- **Protocol**: Interface 2, magic bytes `0x46 0x23 0xEA`, 65-byte HID Feature Reports, based on OpenRGB reverse-engineering

### Custom RGB Window: Layout Adaptability

- `CustomRGBWindow` now accepts layout parameters, automatically adapts to the connected keyboard:
  - **Everest Max**: Full layout with numpad, nav cluster, and 45 side LEDs
  - **Everest 60**: Compact 60% layout (61 keys, no numpad, no side LEDs, no "Persist to Slot")
- Separate per-key config and presets per keyboard model, settings don't interfere

### USB Access / udev Rules

- Updated `99-mountain.rules` with all supported devices: Everest Max (`0x0001`), Makalu Max (`0x0002`), Makalu 67 (`0x0003`), Everest 60 ANSI (`0x0005`), Everest 60 ISO (`0x0006`), DisplayPad (`0x0009`)
- Added `hidraw` rules for all devices (previously only DisplayPad had hidraw access)
- Updated README installation instructions with complete udev rules

### Build

- Added `everest60-controller` binary to AppImage
- Added `everest60-controller.spec` for PyInstaller builds

---

## [1.6.2-beta] - 2026-03-28

### Makalu Max (PID 0x0002): Full Support

- **Automatic detection**: App detects Makalu Max and Makalu 67 automatically on startup, same panel, same controls
- **8-button remapping**: Makalu Max supports 8 programmable buttons (vs 6 on Makalu 67); remap and sniper assignments extended accordingly
- **Model name display**: Switcher button and RGB Lighting section header show the detected model name ("Makalu 67" or "Makalu Max")

### DisplayPad: Brightness Control

- **Brightness dropdown** (☀ 0%/25%/50%/75%/100%) added next to the rotation menu, reverse-engineered from USB capture (`12 03 00 00 [%]`)
- Brightness is saved to config and automatically restored on device reconnect or app restart

### UI / UX

- **Device switcher buttons** now turn **green** when the device is connected (instead of always staying gray when not active). Active device stays blue, disconnected stays gray, applies to Keyboard, Mouse, DisplayPad, and OBS
- **DisplayPad busy-at-boot retry**: If the DisplayPad is busy when the app starts (e.g. after autostart), the app retries up to 5× with increasing delays (2 s, 4 s, 6 s, 8 s, 10 s) before giving up

### Build

- Added `makalu-controller` binary to AppImage (was missing, caused errno 2 on Custom RGB in frozen builds)
- Added `build.sh` for reproducible AppImage builds

---

## [1.6.1-beta] - 2026-03-25

### Makalu Max (PID 0x0002): Initial Support

- Device constants and `detect_model()` added to controller
- Default button layout for Makalu Max defined (`REMAP_DEFAULTS_MAX`)

---

## [1.6.0] - 2026-03-25

### Mountain DisplayPad: Full Support

- **Button Images (K1–K12)**: Assign individual 102×102 images or animated GIFs to each of the 12 display buttons
- **Fullscreen Image/GIF**: Upload a single image or animated GIF that spans across all 12 displays as one seamless picture
- **Icon Library**: Built-in library with 39 bundled icons (Media, Social, System, Navigation, Numbers 1–12) plus user-uploaded images, all accessible via a grid picker
- **Fullscreen Library**: Separate library for fullscreen images and GIFs, auto-saves uploaded files for quick reuse
- **Button Actions (K1–K12)**: Assign actions to each button: Shell command, URL, Folder, App, OBS, or Page navigation
- **Multi-Page System**: Create up to 12 sub-pages with customisable folder icons and text labels (DPFolder.png). K1 on sub-pages is always "Back". Fullscreen GIFs work on sub-pages with page navigation still functional underneath
- **Key Event Detection**: Hardware button presses detected via HID (data[0]==0x01 filter, 0.8s debounce). Actions execute during GIF animation by reading key events between frame uploads
- **Icon Rotation**: Rotate all button icons by 0°/90°/180°/270° for mounting the pad in any orientation (e.g. SimRacing setups). Preview thumbnails rotate live in the GUI
- **Device Reconnect**: Automatically re-uploads saved images when the DisplayPad is reconnected or the app restarts
- **Clear All**: Uploads blank (black) images to all buttons on the device, preserving page folder icons
- **Auto-Upload**: Images upload automatically when assigned or when the image dialog is closed, no manual upload button needed
- **GIF Animation**: Supports animated GIFs on individual buttons and fullscreen, with configurable minimum frame time (ms/frame)

### OBS Studio: Global Integration

- **New top-level OBS tab** in the switcher bar (alongside Keyboard, Mouse, DisplayPad)
- OBS connection settings (Host, Port, Password) moved from Keyboard panel to dedicated OBS panel
- Connect & Load Scenes / Disconnect with status indicator
- **OBS switcher button turns green** when connected (visible from any tab)
- **OBS actions available on all devices**: D1–D4 (Keyboard) and K1–K12 (DisplayPad) can be set to OBS type with Scene/Record/Stream selector
- OBS actions execute via `obsws_python` in background threads

### Keyboard (Everest Max): Improvements

- **OBS section removed** from Keyboard panel (moved to global OBS tab)
- **D1–D4 actions**: Added "OBS" action type with scene/record/stream dropdown
- **Auto-save**: D1–D4 action changes save immediately on type change, browse, or entry edit, green checkmark buttons removed

### UI / UX

- **Simplified DisplayPad layout**: Single scrollable panel (no accordion) with all controls directly visible
- **Simplified OBS layout**: Direct content display without accordion
- **Two-row switcher bar**: Keyboard/Mouse/DisplayPad on top, OBS Studio centered below
- **Emoji-free switcher buttons**: Text-only buttons for better compatibility across platforms
- **Window width** increased to 480px to accommodate 4 tabs
- **Scroll speed** capped in all Library Picker dialogs (consistent with panel scroll behaviour)
- **GIF frame picker skipped** for DisplayPad (device supports animation natively)

### Internationalisation

- Full DE/EN support for all new DisplayPad features (29+ new keys)
- OBS panel and action type labels in both languages
- Page system labels: Page selector, Back button, page name hints

---

## [1.5.1] - 2026-03-22

### Internal

- `mountain-time-sync.py`: fixed slow memory growth in the controller loop, `_handle_btn_resp` was redefined on every iteration (5×/s), creating constant function-object churn; moved to a single definition before the loop
- `mountain-time-sync.py`: RAM and HDD metrics now polled every 2 s instead of every 0.2 s, values change slowly and the reduction in `virtual_memory()` / `disk_usage()` allocation pressure stops Python's memory allocator from retaining freed arenas

---

## [1.5.0] - 2026-03-22

### Makalu 67 Mouse: Button Remap

- New **Button Remap** section in the Makalu 67 panel
- Remap any of the 6 physical buttons (Left, Right, Middle, Back, Forward, DPI+) to a different function
- Categories: Mouse, DPI, Scroll, Sniper
- New **DPI Sniper** function: assign a button to temporarily switch to a lower DPI while held, profile DPI is restored automatically on release (no software polling required, handled by mouse firmware)
- DPI Sniper value is configurable via slider + input field (50–19,000, step 50)
- Left button remap includes a 10-second safety confirmation dialog, automatically reverts if not confirmed
- Assignments are saved to config and restored on next launch

### Makalu 67 Mouse: DPI

- DPI settings panel: 5 configurable DPI levels, cycle through them with the DPI button on the mouse
- Reads current DPI values from the mouse on panel open and polls for profile changes every 1.5 seconds
- Reset button restores factory defaults

### Makalu 67 Mouse: Settings

- Mouse settings panel: Polling Rate (125 / 250 / 500 / 1000 Hz), Button Response (debounce 2–12 ms), Angle Snapping (on/off), Lift-Off Distance (Low/High)

### Internationalisation

- Full DE/EN language support for the entire Makalu 67 panel (RGB, Custom RGB, DPI, Settings, Button Remap)
- All section titles, labels, dropdowns, status messages and button grid update live when switching language

### Presets

- 6 built-in color presets ship with the app for both the **keyboard** (Custom RGB) and the **Makalu 67** (Custom RGB): Synthwave, Ocean, Ember, Forest, Arctic, Galaxy
- Presets load automatically on first launch, no setup required

### Internal

- `controller.py`: extracted `_run_cmd()` helper, all HID commands now share a single open/send/get/close pattern instead of duplicating it per function
- `panel.py`: extracted `_fetch_dpi()` helper, `_dpi_load_from_device` and `_dpi_poll` no longer duplicate the subprocess/parse logic
- `panel.py`: removed dead `_REMAP_LABELS` / `_REMAP_LABEL_TO_KEY` class attributes (superseded by i18n translation maps)
- Fixed `rgb code` / `rgb code2` CLI commands in controller.py that would crash at runtime after the `_send_lighting` refactor

---

## [1.4.2] - 2026-03-21 (Beta)

### Makalu 67 Mouse: RGB Control (New Device)
- Full RGB control panel for the Mountain Makalu 67 gaming mouse (VID 0x3282, PID 0x0003)
- Effects: Static, Breathing, RGB Breathing, Rainbow, Responsive, Yeti, Off
- Dual-zone color support for Breathing and Yeti (Zone 1 + Zone 2 colors)
- Speed control: Slow / Medium / Fast (confirmed via USB capture)
- Brightness: 5 levels, 0 / 25 / 50 / 75 / 100 (dropdown, confirmed via USB capture)
- Rainbow direction: ← / → (confirmed via USB capture)
- 12 color presets (standard gaming colors), click to apply instantly
- All controls push to the mouse immediately without a separate Apply button
- UI shows only the controls relevant to the selected effect

### Keyboard Main Display
- Added **Volume** mode to the display mode selector

### D1–D4 Image Upload
- Fixed upload checksum: was hardcoded `0x6be9`, now correctly computed from pixel data
- Added debug log file at `/tmp/basecamp_d1d4_upload.log` for troubleshooting upload issues

### Internal
- Device code restructured into `devices/everest_max/` and `devices/makalu67/`
- Shared utilities extracted to `shared/` (config, image_utils, ui_helpers)
- Protocol documentation moved to `protocol/`
- README screenshots moved to `docs/`

---

## [1.4.1] - 2026-03-19

### Upload Images & Image Library
- New **Upload Images** dialog (Numpad Keys section): shows D1–D4 as four tiles with thumbnail previews, select images per slot and upload all at once with **Upload All**
- Per-slot **↑** button inside the dialog for uploading a single slot without affecting others
- **Image Library**: every uploaded image is automatically saved as a thumbnail locally: pick previously used images with one click instead of browsing the file system every time
- Library images can be deleted individually via the ✕ button
- The last uploaded image per D-slot is remembered and shown as the tile preview on next open
- **Skip detection**: if the same image is selected again (content unchanged), the slot is skipped, no unnecessary flash write, both in single and multi upload
- **Main display Image Library**: the main display upload now also uses the library picker with thumbnails in the correct 240×204 aspect ratio (stored in `main_library/`)
- Image Library picker opens at the mouse cursor position

---

## [1.4.0] - 2026-03-19

### Custom RGB Mode
- Completely redesigned: new per-key color editor with a full keyboard canvas in a popup window
- Click individual keys to select and color them
- Rubber band (drag) selection across multiple keys
- Ctrl+click and right-click for toggle selection
- Alt+click eyedropper to sample a key's current color
- Ctrl+Z / Undo button (up to 20 steps)
- Side LEDs shown as individual clickable squares around both keyboard and numpad bezels (11 top, 4 right, 12 bottom, 4 left; numpad: 3 top, 4 right, 3 bottom, 4 left)
- Fill selected, fill all, select all, deselect all controls
- Preset system: save, load and delete named color presets
- Built-in **Synthwave** sample preset included
- Section renamed from "Custom RGB Mode (Beta)" to "Custom RGB Mode"

### Color Picker
- Replaced the system color dialog with a custom HSV color wheel
- Circular picker: hue as angle, saturation as radius, brightness as slider
- Before/after preview swatches and hex input field
- Used everywhere colors are picked: Key Color Editor, RGB Lighting, Custom RGB zones

### Bug Fixes
- Fixed: Direction dropdown visible on startup when Static effect was selected
- Fixed: Custom RGB colors not applying to keyboard in AppImage, `basecamp-controller` was not rebuilt with `per-key-rgb` support
- Fixed: Synthwave preset not loading side LED colors, wrong JSON key (`side_leds` → `side`)

---

## [1.3.1] - 2026-03-18

### Numpad Keys: Action Types
- Added action type selector per D-button: Shell, URL, Folder, App, None
- New folder picker: opens native file manager dialog to browse for a folder
- New app picker: searchable list of installed `.desktop` applications
- Actions are saved immediately to config when ✓ is pressed, no restart required
- New **Reset Buttons Flash** button: overwrites all 4 keyboard flash slots with your configured actions. Use this after first setup or when switching from Windows Mountain Base Camp, as BaseCamp may have stored its own actions in flash that cause two actions to fire on a single button press

### OBS Integration
- Removed per-button ✓ save button, type and scene changes now save automatically

### Bug Fixes
- Fixed: D4 button press not detected (Write 2/3 in `_write_action` was disabling the flash slot before byte42 could activate)
- Fixed: `XDG_RUNTIME_DIR` not set when launching apps/folders from D-button press as sudo user
- Fixed: Folder/App actions not working on Arch/CachyOS/KDE: the controller now auto-detects Wayland vs X11 and sets the correct display environment (`WAYLAND_DISPLAY` or `DISPLAY`)

### Code Quality
- All CLI error messages changed from German to English

---

## [1.3.0] - 2026-03-17

### RGB Lighting
- Fully implemented RGB effects: Wave, Tornado, Tornado Rainbow, Reactive, Yeti, Matrix, Off
- Fixed inverted speed slider (hardware uses 1=fast, 100=slow, now correctly mapped)
- Fixed Tornado and Tornado Rainbow effects not working
- Direction dropdown is now context-sensitive: arrow directions (L→R, T→B, …) for Wave effects, CW/CCW for Tornado effects
- RGB settings (effect, speed, brightness, colors, direction) are now saved to config and restored on next launch

### Custom RGB Mode (Beta)
- New section: zone-based RGB colors for 7 keyboard zones (F Keys, Number Row, QWERTY, Home Row, Shift Row, Bottom Row, Numpad)
- Side ring LED color control (30 LEDs on keyboard, 14 on numpad)
- Brightness slider for all LEDs
- Reset button to restore all zones to default colors
- Zone colors and brightness are saved to config and restored on next launch

### GUI
- Reordered accordion sections: Monitor → Main Display → Numpad Keys → RGB Lighting → Custom RGB Mode → OBS Integration
- OBS Integration moved to the bottom
- All red buttons now have bold black text for better readability
- All colored buttons (blue, green) now use white text instead of near-black
- GIF frame picker cancel button text changed from muted gray to white

### Bug Fixes
- Fixed: switching back to Clock mode after a main display image upload now works correctly
- Fixed: main display stuck on Mountain logo can now be resolved directly in the app via the **Reset Dial Image** button, no Windows required

### Config Persistence
- RGB settings saved to `~/.config/mountain-time-sync/rgb_settings.json`
- Zone colors saved to `~/.config/mountain-time-sync/zone_colors.json`

---

## [1.2.0]

- AUR package for Arch / CachyOS / Manjaro
- Two AppImages: Debian/Ubuntu and Fedora/Nobara builds
- Fixed udev rule: use `MODE=0666` for Arch/CachyOS compatibility

## [1.1.0]

- Main display upload (240×204 image)
- Main display mode switch (Image / Clock)
- Reset Dial Image button
- GIF frame picker for D1–D4 image upload

## [1.0.0]

- Initial release
- Time sync (analog / digital clock)
- Monitor mode: CPU, GPU, RAM, HDD, Network metrics
- D1–D4 button actions and image upload (72×72)
- OBS WebSocket integration
- System tray support
- DE / EN language support
