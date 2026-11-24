"""Operating-system seams of the core.

Everything that differs between Linux and Windows goes through a module in
here, so that the rest of the core reads the same on both. A module in this
package imports OS-specific modules (pwd, winreg, ...) inside the functions
that need them, never at the top, so the package imports everywhere.
"""
import sys

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
