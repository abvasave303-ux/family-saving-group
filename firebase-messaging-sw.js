importScripts(
  "https://www.gstatic.com/firebasejs/12.18.0/firebase-app-compat.js"
);

importScripts(
  "https://www.gstatic.com/firebasejs/12.18.0/firebase-messaging-compat.js"
);

firebase.initializeApp({
  apiKey: "AIzaSyC0ISJyPPbn0I-uA49Dku95PFg4D5TVUnI",
  authDomain: "family-saving-group.firebaseapp.com",
  projectId: "family-saving-group",
  storageBucket: "family-saving-group.firebasestorage.app",
  messagingSenderId: "857357671416",
  appId: "1:857357671416:web:de5586c374dec2d3be540d"
});

const messaging = firebase.messaging();
