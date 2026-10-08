import urllib.request

class NoRedirection(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response
    https_response = http_response

opener = urllib.request.build_opener(NoRedirection)
req = urllib.request.Request('https://tender.civenta.co.uk/index.html', headers={'User-Agent': 'Mozilla/5.0'})
res = opener.open(req)
print('Status without following redirect:', res.getcode())
print('Headers:', dict(res.headers))
