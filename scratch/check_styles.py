import urllib.request

url = 'https://tender.civenta.co.uk/styles.css'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
res = urllib.request.urlopen(req)
print('styles.css status:', res.getcode())
css = res.read().decode('utf-8')
print('styles.css length:', len(css))
print('styles.css preview:', css[:200])
