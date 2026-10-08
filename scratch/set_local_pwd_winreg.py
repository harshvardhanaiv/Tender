import winreg

key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
winreg.SetValueEx(key, "LOCAL_DB_PASSWORD", 0, winreg.REG_SZ, "postgres")
winreg.CloseKey(key)
print("LOCAL_DB_PASSWORD written to HKCU\\Environment successfully.")
