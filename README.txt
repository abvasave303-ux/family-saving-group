# Family Saving Group — Online-ready Application

## क्या है
यह backend + database वाला prototype है। Laptop/मोबाइल browser में एक ही server से चल सकता है।
डेटा SQLite database में सुरक्षित रहता है।

## Laptop पर चलाने के लिए
1. Python 3.10+ install करें.
2. इस folder में terminal खोलें.
3. `pip install -r requirements.txt`
4. `python server.py`
5. Laptop में Chrome/Edge खोलें और `http://localhost:8000` जाएँ.
6. उसी Wi‑Fi पर मोबाइल से चलाना हो तो laptop का local IP, जैसे `http://192.168.1.10:8000`, खोलें.

Demo Admin PIN: 1234

## Important
यह अभी prototype है। Internet पर public deployment से पहले secure login, HTTPS, password hashing, role permissions, automatic backups, audit log और production-grade interest/EMI rules जोड़ना जरूरी है.

## Interest rule
Loan rate: 2% per month.
Interest distribution: total savings के अनुपात में.
Payment prototype में payment amount का पहले current principal पर 2% interest और शेष principal में समायोजन किया गया है; production में आपके वास्तविक loan नियम के अनुसार इसे finalize करना चाहिए.
