import winreg
import os

key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
winreg.SetValueEx(key, "LIVE_DB_HOST", 0, winreg.REG_SZ, "62.171.162.135")
winreg.SetValueEx(key, "LIVE_DB_PORT", 0, winreg.REG_SZ, "5440")
winreg.SetValueEx(key, "LIVE_DB_NAME", 0, winreg.REG_SZ, "postgres")
winreg.SetValueEx(key, "LIVE_DB_USER", 0, winreg.REG_SZ, "postgres")

# Read DB_PASSWORD from .env.production
env_prod = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env.production"))
with open(env_prod, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line.startswith("DB_PASSWORD="):
            pwd = line.split("=", 1)[1]
            winreg.SetValueEx(key, "LIVE_DB_PASSWORD", 0, winreg.REG_SZ, pwd)
            break
winreg.CloseKey(key)
print("Environment variables written to HKCU\\Environment successfully.")
