import urllib.request
import json

for ep in ['/api/health', '/api/config/public']:
    url = f'https://tender.civenta.co.uk{ep}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req)
        print(f'{ep} status:', res.getcode())
        print(f'{ep} response:', res.read().decode('utf-8'))
    except Exception as e:
        print(f'{ep} error:', e)
