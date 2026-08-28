# Family Saving Group — Internet Deployment Package

यह package इंटरनेट पर deploy करने के लिए तैयार किया गया prototype है। इसमें Flask + SQLite + Gunicorn है। Render पर persistent disk लगाया गया है ताकि database file deploy के बाद बनी रहे।

## Deploy
1. इस folder को GitHub repository में upload करें।
2. Render में New → Web Service चुनकर repository connect करें।
3. `render.yaml` के अनुसार service configure करें।
4. `ADMIN_PIN` में अपना मजबूत PIN रखें और `SECRET_KEY` generated रहने दें।
5. Deploy करें।
6. मिलने वाला HTTPS URL laptop और mobile दोनों में खुलेगा।

## Local
`pip install -r requirements.txt`
`python server.py`
फिर `http://localhost:8000` खोलें।

## Public करने से पहले
- 23 परिवारों के असली नाम/मोबाइल भरें
- Member Login और Admin/Member permissions जोड़ें
- PDF/Excel reports जोड़ें
- automatic backups और audit log लगाएँ
- loan/interest accounting rules लिखित रूप से final करें
