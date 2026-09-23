"""Le limiteur de débit de `SerperClient` espace les requêtes MÊME entre threads.

Le backend garde une instance par clé, partagée entre appels concurrents (oto#115) :
sans verrou, les threads lisent le même instant de dernière requête, dorment le même
temps et partent ensemble — la limite déclarée ne tient plus. Aucun appel réseau.
"""
import threading
import time

from oto.tools.serper.client import SerperClient


def test_des_threads_concurrents_sont_espaces_de_l_intervalle():
    client = SerperClient(api_key="k")
    client._min_interval = 0.05
    departs, barriere = [], threading.Barrier(5)

    def tir():
        barriere.wait()
        client._rate_limit()
        departs.append(time.monotonic())

    fils = [threading.Thread(target=tir) for _ in range(5)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    departs.sort()
    ecarts = [b - a for a, b in zip(departs, departs[1:])]
    assert min(ecarts) >= 0.04, ecarts
