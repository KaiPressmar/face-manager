# Face Manager · Facelift 0.18.0

Die Designprüfung beginnt bei drei häufigen Aufgaben. Ein Foto oder eine Reise wiederfinden, eine Zuordnung bearbeiten und zu einer früheren Ansicht zurückkehren. Frühere Tests auf 0.17.1 zeigten eine weit unten beginnende Karte, schlecht sichtbare Beschriftungen und unterschiedliche Regeln für Dialoge, Filter und Seitenwechsel. Die unabhängige Nachprüfung hat die konkreten Fälle in Navigation, Bildauswahl und Datenänderungen geprüft.

## Gestaltung und Orientierung

- Die Seitenleiste gibt immer einen Einstieg zu Bilder, Personen, Karte, Dateinamen und Einstellungen. Bei engen Fenstern enthält „Mehr“ die beiden Werkzeuge. Die Arbeitsfläche bekommt mehr Breite. Aktive Bereiche und der Tastaturfokus sind klar unterscheidbar. [Apple zu Seitenleisten und Werkzeugelementen](https://developer.apple.com/design/human-interface-guidelines/sidebars) · [Microsoft Fluent 2 Layout](https://fluent2.microsoft.design/layout)
- Farben und Typografie folgen den vorhandenen Hell- und Dunkel-Themen. Sekundäre Texte erhalten mehr Kontrast. Kurze, beschriftete Hauptaktionen stehen nahe bei der jeweiligen Aufgabe. Technische Angaben und seltener verwendete Darstellungsoptionen sind aufklappbar. Bei reduzierter Bewegung erscheint der neue Bereich ohne Seitenanimation. [Fluent 2 Typografie](https://fluent2.microsoft.design/typography) · [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- Der Textfarbwert für sekundäre Beschriftungen erreicht auf der hellen Grundfläche 5,14:1, auf Weiß 5,82:1 und auf dem dunklen Panel 6,94:1. Helle und dunkle Oberfläche sind im Foto- und Kartenablauf geprüft.
- Die Fotokarte beginnt mit den beiden Aufgaben „Standorte“ und „Verlauf“. Zeit und Bereich bleiben leicht erreichbar. Herkunft und Stand der Ergebnisse bekommen dieselben sichtbaren Hinweise in Karte und Galerie. Die Druckvorschau zeigt auch bei schmalen Fenstern früh das Ergebnis. Beim Neuberechnen bleiben Seitenwahl und letzter Stand verständlich. Die Trennung von Fotos, Filtern und Detailansicht lässt sich auch in anderen Fotoanwendungen beobachten. [Adobe Lightroom Classic](https://helpx.adobe.com/lightroom-classic/desktop/viewing-photos/view-photos.html) · [Nielsen Norman Group](https://www.nngroup.com/articles/progressive-disclosure/)
- „Schnellwechsel“ ist der zusätzliche kurze Weg. Er zeigt bekannte Bereiche, findet verwandte Begriffe wie „Reisealbum“ und „Sicherung“ und lässt sich per Maus oder Strg+K öffnen. Alle Ziele folgen der normalen App-Navigation. Die Suche greift auf die Bezeichnungen der Bereiche zu. [Microsoft PowerToys Command Palette](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/overview)
- Bei einem ersten Start hat „Bilderordner auswählen“ Vorrang. Einführung und Versionshinweise bleiben über die App erreichbar. In den Einstellungen steht eine Rückmeldung direkt bei der Aktion. Im Fenster „Aktivitäten“ sind Fortschritt, Pausieren, Fortsetzen und Abbrechen beschriftet. [Fluent 2 Einstieg](https://fluent2.microsoft.design/onboarding) · [W3C zu älteren Nutzerinnen und Nutzern](https://www.w3.org/WAI/older-users/)

## Vier verschiedene Wege

| Befehl | Wirkung |
| --- | --- |
| Zurück und Vorwärts | Kehren zu einer konkreten früheren Ansicht zurück. Zwei Ansichten derselben Adresse können getrennte Filter, Auswahl und Scrollposition haben. Die App prüft bei einem Bibliothekswechsel alte Auswahlen. |
| Bereich über die Seitenleiste oder den Schnellwechsel öffnen | Nimmt die zuletzt benutzte Ansicht des Bereichs wieder auf. Ein direkter Verweis aus einem Foto oder Album öffnet seinen ausdrücklich gewählten Zusammenhang. |
| Zur Ausgangsansicht | Öffnet Bilder, Personen, Karte, Dateinamen oder Einstellungen mit den jeweiligen Standardansichten. Der vorherige Stand ist über Zurück erreichbar. Gerätepräferenzen wie Theme und Kartenstil gelten weiter. |
| Rückgängig und Wiederholen | Kehren eine aufgezeichnete lokale Namenszuordnung oder Archivaktion an exakt den betroffenen Gesichtern um und wenden sie erneut an. Eine neue Aktion beendet die vorherige Wiederholung. Eine inzwischen bearbeitete Zuordnung löst einen Konflikthinweis aus. |

Die erste Zurück-Aktion bei einem offenen Dialog behandelt die oberste Ebene. Vorwärts löst keine alte Bestätigung erneut aus. Geänderte Entwürfe bekommen beim Schließen einen eigenen Rückweg. Die App verwendet die vorhandenen Hash-Routen, Fokusregeln und Browser-Historie. Bedienelemente zum Zurücksetzen von Filtern, zum Aufheben einer Auswahl und zum Wechsel der Karte bleiben am passenden Ort. [Microsoft Windows-Navigation](https://learn.microsoft.com/en-us/windows/apps/develop/ui/navigation/navigation-history-and-backwards-navigation) · [W3C-Dialogmuster](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)

Die Historie gespeicherter Zuordnungen ist auf die lokale Bibliothek begrenzt. Der neue Personenname bleibt wiederverwendbar. Schreibender Geräteabgleich, Umbenennen von Originaldateien und vollständige Bibliothekswiederherstellung haben eigene Sicherungs- und Konfliktregeln.

## Abnahme

- Bilder mit Album- und Personenfilter öffnen, nach unten scrollen, den Bereich wechseln und mit Zurück, Vorwärts und Seitenleiste vergleichen. Zwei Ansichten mit gleicher Adresse getrennt speichern.
- Reisealbum aus Bilder zur Karte öffnen. Danach Filter oder Ausschnitt zurücksetzen, den vorherigen Stand wieder öffnen und die URL nach Neuladen prüfen.
- Hilfe, Galerie, Importdialog, Dateinamenvorschau und Fotobuchentwurf über Zurück und Escape schließen. Für A → B → C nach Rückkehr zu B die Hilfe öffnen, schließen und C per Vorwärts wiederfinden.
- Während eines Filterwechsels eine verspätete Antwort auslösen. Prüfen, dass keine Auswahl mit den alten Fotos gespeichert wird. Ein aussortiertes Gesicht aus der Galerie öffnet die richtige Kategorie.
- Namenszuordnung und Aussortieren rückgängig machen und wiederholen. Änderungen im zweiten Fenster, Hintergrundgruppierung, eine neue Aktion und den Wechsel der Bibliothek prüfen.
- Am Desktop und bei 320 sowie 390 Pixeln in beiden Farbmodi testen. Tastatur, Überschriften, Einsatz ohne Hover, bei vergrößerter Schrift und langsamem Laden prüfen. Für native Windows- und Android-Abläufe gelten zusätzliche Geräteprüfungen.

## Weitere sinnvolle Ideen

Gespeicherte Ansichten für häufige Filter, ein gruppierter Kartenüberblick für dichte Reisen und Tagesseiten im Fotobuch sind mögliche nächste Ausbauten. Ein Prototyp auf denselben Testdaten soll vorab zeigen, ob er weniger Schritte oder sichtbar bessere Orientierung bietet.

[Überarbeitete Karte](../screenshots/navigation-0180.webp) · [Schnellwechsel](../screenshots/quick-switch-0180.webp) · [Fotobuchvorschau](../screenshots/photobook-0180.webp)
