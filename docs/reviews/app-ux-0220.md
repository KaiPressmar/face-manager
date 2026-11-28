# Gestaltung und Bedienung in Face Manager 0.22.0

Die häufigsten Aufgaben beginnen mit dem Import, der Prüfung von Gesichtern und dem Wiederfinden eines Fotos. Die Überarbeitung hält diese Wege kurz, macht Fehler und offene Entscheidungen sichtbar und hält Auswahl sowie Fokus beim Navigieren stabil. Die Prüfung umfasst Bildschirmbreiten von 320 bis 1440 Pixeln, Hell und Dunkel, Tastatur und Touch sowie die nativen Abläufe für Start und Export.

## Navigation und visuelle Hierarchie

Auf schmalen Bildschirmen teilen sich Navigation, Verlauf und Aktionen den Platz oberhalb der Arbeitsfläche. Die Kopfleiste gruppiert Import und Aktivitäten links und Hilfe, Hardware und Darstellung rechts. Die Farbauswahl öffnet direkt alle drei verfügbaren Modi. Die gewählte Einstellung gilt sofort und kann nach einem Speicherfehler erneut gespeichert werden. Die Einstellungen bieten auf schmalen Bildschirmen eine kompakte, beim Scrollen erreichbare Bereichsauswahl. [Darstellung](../screenshots/appearance.webp) · [Schmale Einstellungen](../screenshots/settings-compact.webp)

## Bilder, Alben und Personen

In der Fotoliste konkurrieren Auswahlzustand, Filter, Alben und das Öffnen eines Bildes um Aufmerksamkeit. Die Auswahl bleibt mit ihrem Umfang an der Liste sichtbar. Ein geöffneter Bildausschnitt und die Auswahl bleiben auch bei Aktualisierungen erreichbar. Beim langen Scrollen erzeugt die App Karten für den sichtbaren Bereich und dessen Umgebung. Die Bilderliste lädt geänderte Daten bei Bedarf nach. Tastaturbedienung, sichtbare Auswahl, die Rückkehr aus der Vollbildansicht und die Albumzugehörigkeit bleiben bei schmalen Fenstern geprüft. [Fotobibliothek](../screenshots/photo-library.webp)

In der Gesichtsprüfung erschien bei Serverfehlern dieselbe Leermeldung wie nach erledigter Prüfung. Gruppenliste, Details und Archiv melden nun Ladefehler an Ort und Stelle. Die Wiederholung setzt die Prüfung am gewählten Eintrag fort. Nach einer Bibliothekswiederherstellung werden ältere Gruppendetails und ausstehende Anfragen verworfen. Änderungen an ausgewählten Gesichtern prüfen Bibliothek und Zuordnungsstand. Die Abnahme verwendet zwei Bibliotheken mit gleich nummerierten Gruppen und unterschiedlichen Fotos. [Schmale Gesichtsprüfung](../screenshots/people-review-compact.webp)

## Import und Aktivitäten

Lange Ordnernamen und verschachtelte Scrollbereiche erschwerten es, betroffene Pfade und Aktionen zu finden. Das Aktivitätenfenster scrollt als Einheit. Seine Schließen-Schaltfläche bleibt erreichbar. Die Historie ist beim Start verfügbar und zeigt übersprungene Dateien und Ordner mit Grund. Liegt eine vollständige Pfadliste vor, lassen sich die Einträge gezielt erneut prüfen. Importe werden nach einer Unterbrechung fortgesetzt, eine Pause bleibt bestehen und bestätigte Abbrüche werden gespeichert. Aus einer wiederhergestellten Sicherung übernommene offene Importe bleiben in Pause. [Aktivitäten](../screenshots/activities.webp)

## Karte, Reisen und Fotobuch

Bei 390 Pixeln Breite begannen die Karteninhalte weit unten, während der Export im schmalen Fotobuchdialog erst nach Scrollen erreichbar war. Die Kartendetails erscheinen unter der Karte. In der erfassten Ansicht beginnt die Karte dadurch rund 79 Pixel weiter oben. Zwei Finger zoomen um ihren Mittelpunkt, auch bei aktiver Bereichsauswahl. Markertippen, Verschieben und Auswahl bleiben eigene Gesten. Die Beschreibung der Aufnahmedaten bleibt am ausgewählten Reisestopp sichtbar. Exportaktionen sitzen neben der Vorschau. Auf Android führt die Auswahl in den Speicherortdialog des Systems. [Schmale Karte](../screenshots/photo-map-compact.webp) · [Fotobuch](../screenshots/photobook.webp)

Die erste detaillierte Zoomstufe verwendet rund 257 kB komprimiertes Kartenmaterial im Produktionsbuild. Vorher wurden dort rund 391 kB geladen. Weitere Gewässer und Ortsnamen werden erst bei Bedarf geladen. Die entwicklungsseitige Transfermessung ist getrennt vom Produktionsbuild. Geprüft wurden Offline-Karte, simulierte Online-Kacheln, mehrere Reisetage, Seitenvorschau und PNG- und ZIP-Ausgabe. [Kartenansicht](../screenshots/photo-map.webp) · [ZIP-Export](../screenshots/photobook-zip.webp)

## Dateinamen und Wartung

Bei der Dateiauswahl saß die Rückmeldung unterhalb der Vorschauaktion. Die Zeilen boten für die Auswahl kleine Kontrollkästchen. Die Auswahl steht jetzt neben der Vorschauaktion und die Zeilen bieten größere Touch-Ziele. Bei einem Fehler lässt sich die Vorschau im Dialog wiederholen. In den Einstellungen werden die Überschriften für Trennzeichen und ihre Beispiele in lesbarer Breite angezeigt. Fehlende Originale lassen sich zählen, bevor eine Bereinigung bestätigt wird. [Dateinamen](../screenshots/filenames.webp) · [Benennungsschema](../screenshots/settings-compact.webp)

Die Sicherungsvorschau zeigt Datei, Datum und Umfang sowie den Weg zum bisherigen Stand. Android zeigt Vorbereitung, Zielauswahl und Schreiben als eigenen Vorgang. Die Bestätigung erscheint nach dem Schließen der Zieldatei. Das Startfenster verwendet auf Desktop deutsche Meldungen und hält den Protokollpfad bei schmaler Breite lesbar. Die native Android-Einführung und Hinweise zu Berechtigungen lassen sich bei großer Schrift scrollen. [Wiederherstellung](../screenshots/restore.webp)

## Leistung und Abnahme

Die synthetische Bibliothek umfasst 40.000 Fotoeinträge und 20.000 Gesichtszuordnungen. Der Median für 40 Einträge lag auf dem Testrechner bei 149 ms für die erste Seite, 249 ms ab Position 39.000 und 182 ms mit Personenfilter. Gemessen wurde der Backend-Aufruf mit geleertem Anwendungscache. Im Chromium-Test mit 40.000 gemeldeten Fotos waren rund 2.000 Fotos nachgeladen. Im tiefen Listenbereich waren 22 Karten am Desktop und 24 bei 390 Pixeln im DOM. Die Browserwerte stammen aus Testdaten mit simulierten Seitenantworten.

Die Browserprüfungen kombinieren reguläre Abläufe mit gezielten Fehlern, Bibliothekswechseln und einer simulierten Android-Speicherschnittstelle. Für das Freigeben des Android-Exports sind zusätzlich APK und realer Systemdialog relevant. Die Freigabeprüfungen für Windows und Ubuntu bauen die Desktop-Pakete. Zwei SMB-Mounts und der Wechsel zwischen Windows und Ubuntu sind Gegenstand eigener CI-Abläufe.
