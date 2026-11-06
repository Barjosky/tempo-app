/* Service worker : il rend la page installable et lisible hors ligne, rien de plus.

   RESEAU D'ABORD, toujours. Une prevision n'a de valeur que fraiche : servir le cache
   en priorite afficherait les couleurs d'hier a qui ouvre l'appli ce matin. Le cache
   n'est qu'un repli, quand le reseau ne repond pas -- et la page affiche alors la date
   du dernier calcul, qui dit d'elle-meme que la donnee est ancienne.

   Les requetes vers d'autres domaines (API Tempo, compteur, polices) ne passent pas
   par ici : on ne met en cache que ce que le site publie lui-meme. */
const CACHE = "tempo10";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(
    fetch(req)
      .then((rep) => {
        if (rep.ok) {
          const copie = rep.clone();
          e.waitUntil(caches.open(CACHE).then((c) => c.put(req, copie)));
        }
        return rep;
      })
      .catch(() => caches.match(req, { ignoreSearch: true })
        .then((r) => r || Response.error()))
  );
});
