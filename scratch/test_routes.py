import urllib.request

urls = [
    'https://tender.civenta.co.uk/static/index.html',
    'https://tender.civenta.co.uk/index.html',
    'https://tender.civenta.co.uk/app.js?v=187',
    'https://tender.civenta.co.uk/login.html'
]

for url in urls:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        res = urllib.request.urlopen(req)
        print(f'{url} -> {res.getcode()} (Final: {res.geturl()})')
    except urllib.error.HTTPError as e:
        print(f'{url} -> HTTPError {e.code}')
    except Exception as e:
        print(f'{url} -> Error {e}')
