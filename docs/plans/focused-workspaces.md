# Arbeitsflächen für kleine Fenster

Ausgangspunkt ist `develop` nach PR #143. Die Nachprüfung betrachtet typische Aufgaben aus Nutzersicht. Dazu gehören das Wiederfinden von Fotos, das Zuordnen von Gesichtern, die Karten- und Stoppsuche, die Dateinamenvorschau und die Suche nach einer Einstellung. Die Ansicht der Arbeitsgruppe und die Fotokarte wurden zusätzlich unabhängig geprüft. Die Sichtprüfung verwendet Testdaten und Fenster mit 390 × 740, 768 × 600 und 1440 × 900 CSS-Pixeln.

## Befunde und Umbau

| Ansicht | Befund | Änderung |
| --- | --- | --- |
| App-Navigation und Aktivitäten | Bei 390 × 740 Pixeln beginnt der Inhalt erst bei etwa 196 Pixeln. Die Werkzeugzeile kann sich über die Navigation legen. | Verlauf und Werkzeuge teilen sich bei ausreichender Breite eine Zeile. Schmale Ansichten behalten beschriftete Einstiege für Import und Hilfe. Versionshinweise sind dort unter „Mehr“ erreichbar. |
| Bilder und Galerie | Die offene Filterleiste beansprucht im schmalen Fenster mehrere Zeilen, bevor ein Foto sichtbar wird. Die Galerie hat bereits eine eigenständige Vollbildansicht. | Treffer, Alben und Auswahl bleiben vorn. Filter, Sortierung und Ansichtsoptionen lassen sich in kompakten Fenstern aufklappen. Ein Zähler macht aktive Filter sichtbar. |
| Personen | Unter „Ohne Namen“ steht in kleinen Fenstern die ganze Gruppenliste vor den Gesichtern. Bei 768 × 600 Pixeln begann die Gesichterliste erst bei etwa 1.250 Pixeln. Auch in breiten Fenstern verdrängen mehrere Werkzeugzeilen die Fotos. | Eine kompakte Ansicht zeigt wahlweise die Gruppenliste oder eine Gruppe mit ihren Gesichtern und Aktionen. Die Gruppe lässt sich über die Adresszeile und den Verlauf wieder öffnen. Breite Fenster behalten die Liste neben dem Arbeitsbereich. |
| Fotokarte | Datumsfelder ohne gewählten Zeitraum, Darstellungsoptionen und Export konkurrieren mit der Karte. Bei 390 × 740 Pixeln beginnen die Fotoergebnisse rund 940 Pixel vom Fensterrand entfernt. | Die Karte und ihre Ergebnisse sind auf kleinem Raum direkt umschaltbar. Kartenfilter und eigener Zeitraum brauchen eine bewusste Auswahl. Export bleibt bei der Kartendarstellung erreichbar. |
| Dateinamen | Einleitung, Filter und Auswahlerklärung beanspruchen Platz, bevor die Dateiliste beginnt. | Die Kopfzeile erläutert die Aufgabe in einem Satz. Die gleiche kompakte Filterleiste wie unter „Bilder“ hält die Ergebnisse früher im Blick. |
| Einstellungen | Das schmale Inhaltsverzeichnis beansprucht fünf Zeilen vor der ersten Einstellung. | Die vorhandenen Bereiche bleiben direkt anwählbar. Zwei Spalten verkürzen das Inhaltsverzeichnis auf kleinen Bildschirmen. |

Die Gestaltung verwendet je nach Platz eine oder zwei Arbeitsflächen. Häufige Aufgaben erhalten einen sichtbaren Einstieg. Zusatzoptionen stehen unmittelbar bei der passenden Aufgabe. Das entspricht der adaptiven Liste mit Detailansicht und der schrittweisen Offenlegung sekundärer Funktionen. [Material Design](https://m3.material.io/foundations/layout/canonical-examples/overview) · [Nielsen Norman Group](https://www.nngroup.com/articles/progressive-disclosure/)

Bei 320 CSS-Pixeln müssen Texte und Bedienelemente ohne horizontales Scrollen lesbar bleiben. Die Karte selbst bleibt als zweidimensionale Arbeitsfläche bedienbar. Ziele behalten sichtbare Beschriftungen oder zugängliche Namen und ausreichend Abstand. [W3C Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow) · [W3C Zielgröße](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) · [Apple Human Interface Guidelines für Karten](https://developer.apple.com/design/human-interface-guidelines/maps)

## Abnahme

In der Nachmessung beginnt die Gesichterliste nach dem Öffnen einer Gruppe bei etwa 390 statt 1.250 Pixeln in einem Fenster mit 768 × 600 Pixeln. Bei 390 × 740 Pixeln beginnt der Kartenbereich mit den Fotoergebnissen nach dem Umschalten bei etwa 270 statt 940 Pixeln. Die Messung verwendet die mitgelieferten Testdaten.

- Auf schmalen und niedrigen Fenstern eine Gruppe öffnen, die Gesichter prüfen und zur fokussierten Gruppe in der Liste zurückkehren. Direkte Adressen, Zurück, Vorwärts und den Wechsel zu einer anderen Ansicht prüfen.
- Von der Karte zu Fotos und Reisestopps wechseln. Eigene Zeiträume, Bereichsauswahl und Fotobuchexport einschließlich Rückkehr per Tastatur prüfen.
- In Bilder und Dateinamen einen Filter setzen, das Menü schließen und den aktuellen Filterstand weiterhin erkennen. Für die Hauptansichten auf überlappende Kopfbereiche und horizontales Scrollen prüfen.
- Die vorhandenen Browserabläufe für Vorschläge, Galerie, Import, Aktivitäten, Alben, Einstellungen und Fensterverlauf erneut ausführen.
