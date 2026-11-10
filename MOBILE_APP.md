# Mobile app: iPhone aur Android

Employee apne phone se **Check IN / Check OUT** karta hai. Time hamesha **server ka** lagta hai (phone ka time badalne se kuch nahi hota).
Office ki location settings mein set karein to sirf office ke qareeb (jaise 200 m) check-in ho sakta hai.

> **Sab se pehle:** app ko phone par chalane ke liye software **live (HTTPS wale domain par)** hona zaroori hai (`DEPLOY.md`).
> `http://127.0.0.1` sirf aap ke computer par chalta hai, phone par nahi. Location aur "app install" dono ke liye HTTPS lazmi hai.

## Admin ka set-up (ek dafa)
1. **Admin → Settings**: office ke andar khade ho kar phone se Settings kholein, **Use my current location** dabayen, phir:
   - "Employees can check in / out only near the office" ka tick lagayen
   - Allowed distance 100-300 m rakhein, Office start time aur Late grace minutes set karein, **Save**.
2. Har employee ka login banayein (Employees → naam → **Create login**) aur link + username/password dein.
3. Roz ke ikhtitam par **Attendance → Mark no-shows absent** dabayen (jis ne check-in nahi kiya wo Absent ho jata hai, salary mein katoti is se lagti hai).
   Jin ke paas smartphone nahi, un ki hazri HR usi page se lagata hai.

## Raasta 1 (iPhone + Android dono, free, sab se aasan): browser se app install
App ko phone ki home screen par icon bana dein; yeh bilkul app ki tarah khulti hai.

**Android (Chrome):** site kholein → menu (⋮) → **Install app** (ya *Add to Home screen*).
**iPhone (Safari hi use karein, Chrome nahi):** site kholein → neeche **Share** ka button → **Add to Home Screen** → Add.

Pehli dafa Check IN par phone location ki ijazat maangega: **Allow / While Using** chunein.
Agar ijazat ghalti se Block ho gayi: *iPhone:* Settings → Privacy & Security → Location Services → Safari Websites (ya app ka naam) → While Using.
*Android:* Settings → Apps → Chrome (ya HRMS) → Permissions → Location → Allow.

## Raasta 2: Google Play Store (sirf Android)
Play Store par app **PWA ko Android package mein lapet kar** (Trusted Web Activity) dali jati hai. Is ke liye aap ko yeh chahiye:
- **Google Play Developer account**: ek dafa ka **$25** fee, shanakhti card wagairah se verification.
- Zaroori shart (Google ki official help page ke mutabiq): agar account **personal** hai aur 13 Nov 2023 ke baad bana hai, to production se pehle **closed testing mein kam az kam 12 testers, musalsal 14 din** opt-in rehne chahiye. Organization account ke liye wahan likha nahi, Play Console mein khud check karein.
- Live HTTPS domain, aur privacy policy ka link: **`https://aapka-domain/privacy`** (software mein bani hui hai; Admin → Settings mein Contact email bhar dein).

Steps:
1. **Package banayein** (Android Studio ki zaroorat nahi):
   - **pwabuilder.com** par apna domain daalein → *Package for stores* → **Android → Google Play** → download. Is mein `.aab` file aur **signing key** hoti hai. **Signing key ko mehfooz rakhein**, kho gayi to app update nahi ho sakegi.
   - Ya command line se: `npm i -g @bubblewrap/cli`, phir `bubblewrap init --manifest=https://aapka-domain/manifest.webmanifest`, phir `bubblewrap build`.
   - Package name aisa rakhein jaise `com.aapkicompany.hrms`.
2. **Play Console** mein naya app banayein, `.aab` upload karein (pehle Internal/Closed testing mein).
   Listing ke liye: app naam, short/long description, icon 512x512 (`https://aapka-domain/icon-512.png` se mil jata hai), feature graphic 1024x500, phone ke 2+ screenshots.
   *Data safety* form mein bharein: **Location (precise)** (sirf check-in/out ke waqt) aur **personal info** collect hoti hai, HTTPS se bheji jati hai.
   *App access* mein reviewers ko ek **test login** dein (is ke liye ek dummy employee ka login banayen).
3. **Digital Asset Links** (is ke baghair app ke upar browser ki address bar dikhti rahegi). Server ke environment variables mein lagayen:
   ```
   HRMS_ANDROID_PACKAGE=com.aapkicompany.hrms
   HRMS_ANDROID_SHA256=AA:BB:CC:...      (signing certificate ka SHA-256 fingerprint)
   ```
   Fingerprint: agar Play App Signing on hai (default) to Play Console → *App integrity → App signing key certificate* se lein. Server restart karein aur kholein
   `https://aapka-domain/.well-known/assetlinks.json`: us mein aap ka package aur fingerprint nazar aana chahiye.
4. Testers se 14 din test karwa kar (agar zaroori ho) **Production** ke liye apply karein.

**Be-laag baat:** Google "sirf website" wali apps par aitraaz kar sakta hai, aur yeh app sirf aap ki ek company ke liye hai (login ke baghair kuch nahi dikhta). Isliye reviewers ko test login zaroor dein. Agar sirf apne staff tak rakhni ho, to Play Store par **Closed/Internal testing** ka link bhi kafi hai (staff ke Gmail testers mein add karein), public listing zaroori nahi.

## iPhone par App Store?
App Store ke liye **Apple Developer account ($99 saalana)**, Mac aur Xcode chahiye, aur Apple aksar "sirf website ko app bana kar" daalne wali apps **reject** kar deta hai. Isliye iPhone walon ke liye **Raasta 1 (Add to Home Screen)** hi behtar hai; yeh free hai aur wohi kaam karta hai.

## Mehfooziyat ke baare mein sach
- GPS ko "fake location" apps se dhoka diya ja sakta hai. Location check aam ghalat/door se check-in rokta hai, mukammal rok-thaam nahi. HR ko Attendance page par "kitne meter door se" nazar aata hai, shak ho to wahan dekh sakta hai.
- App ko internet chahiye. Net na ho to check-in nahi hota.
- Employee ki location sirf check-in/out ke lamhe par li jati hai, background mein kabhi nahi (privacy page mein bhi yehi likha hai).

## Notes
Check-in/out and payslips work from the installed PWA on both iPhone (Add to Home Screen) and
Android (Install). Play Store (Android only) needs a TWA built from your live HTTPS domain;
use /privacy as the privacy-policy URL and set HRMS_ANDROID_PACKAGE / HRMS_ANDROID_SHA256.
