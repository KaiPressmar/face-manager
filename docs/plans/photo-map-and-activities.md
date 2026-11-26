# Fotokarte, Reiseauswahl und Aktivitäten

## Einheitliche Bedienung in der gesamten Anwendung

Die Anwendung verwendet drei vertraute Werkzeuge. **Filter** bestimmen, was sichtbar ist. **Auswahl** bezeichnet die Fotos, die bei einer Aktion berücksichtigt werden. **Alben** merken eine Auswahl dauerhaft. In „Bilder“ und „Karte“ zeigen dieselben abwählbaren Chips die aktiven Filter. Ein Wechsel zwischen beiden Ansichten erhält die Auswahl. Zurück, Tastaturfokus, leere Trefferlisten und der Umgang mit inzwischen gelöschten Fotos folgen demselben Verhalten.

| Bereich | Vorhandener Einstieg | Geplante Integration |
| --- | --- | --- |
| Bilder, Ordner und Vollbild | Bilderliste, Galerie und „In Bilder öffnen“ | Eine gemeinsame Fotoauswahl mit Anzahl und „Zum Album hinzufügen“. Album, Person, Ordner, Zeitraum und Gebiet lassen sich kombinieren. Die Oberfläche unterscheidet die sichtbare Seite und alle Treffer. |
| Karte und Reiseverlauf | Standortmarker, gezeichnete Bereiche und Stopps | Derselbe Fotofilter und dieselbe Galerie. „Auswahl öffnen“, „Zum Album hinzufügen“ und „Bild exportieren“ verwenden den angezeigten Umfang. |
| Personen und Gesichter prüfen | Person, Gesichtsauswahl und Vorschlagsprüfung | Aus einer Person heraus Fotos öffnen und sammeln. Bei Änderungen an Gesichtern bleiben die betroffenen Gesichter und Bilder sichtbar. Ein Album kann den Suchraum begrenzen. |
| Dateinamen | Vorschau und Prüfung von Änderungen | Bei Bedarf auf ein Album einschränken. Die Vorschau zeigt die betroffenen Dateien und liefert die Grundlage für die anschließende Bestätigung. |
| Gemeinsame Ordner | Ordnerstatus und Konfliktprüfung | Gemeinsame Statusdarstellung in Aktivitäten. Die Detailansicht behandelt Konflikte und Freigaben. Die Albumfreigabe erfolgt ausdrücklich. |
| Einstellungen, Sicherung und Updates | Einstellungen für Erkennung, Darstellung und Daten | Globale Vorgaben wie GPU-Modus oder Exportformat hier verwalten. Entwürfe behalten ihre abweichenden Einstellungen. Sicherung, Wiederherstellung und Updates behalten ihr direktes Feedback und können ihren Fortschritt zusätzlich in Aktivitäten melden. |

Technisch beginnt die Wiederverwendung bei der vorhandenen Fotoabfrage und dem Transfer „Karte → Bilder“. `navigation.ts` trägt eine konkrete Auswahl im lokalen Navigationszustand. Nach Bibliothekswechsel oder Wiederherstellung werden alte Auswahlen verworfen. Die Fotoabfrage wird um Album und Reisetag ergänzt und serverseitig einheitlich ausgewertet. Bereichsgrenzen, Datumsgrenzen und feste Foto-IDs werden gemeinsam validiert. „Alle Treffer“ umfasst ungeladene Seiten und berücksichtigt abgewählte Fotos. Seitenübergreifende Aktionen nutzen denselben Umfang wie ihre Vorschau. Albumzugehörigkeit referenziert stabile Foto-IDs. Gesicht und Dateifundort behalten eigene IDs.

Die Oberfläche verwendet `LibraryFilterBar` und `FolderFilterModal` für Filter und `FullscreenImageGallery` sowie `useModalFocus` für Bilder und Dialoge. Aus Anwendungsdaten entstehen Vorschläge für Orte, Tage, Titel und Verkehrsmittel. Neue Vorschläge lassen sich prüfen und übernehmen. Bereits gewählte Werte bleiben erhalten. Ändernde Aktionen zeigen vorab ihren Umfang. Dazu testen wir die Wege vom Import zu Karte, Album und Fotobuch sowie **Karte → Bild → Person → zurück**. Weitere Prüffälle sind geänderte Pfade, mehrere Fundorte eines Originals, teils fehlgeschlagene Sammelaktionen und eine Wiederherstellung bei geöffneter Auswahl.

## Auswahl aus der Fotobibliothek

Die Karte soll Zeitraum und Gebiet gemeinsam anwenden. Ein Gebiet bleibt beim Zoomen und Verschieben erhalten. Der Nutzer sieht es als Filter und kann es einzeln entfernen. Ortsvorschläge sollen aus vorhandenen Fotostandorten entstehen. Die Liste zeigt Ort, Anzahl der Fotos und vorhandene Aufnahmetage. Wegpunkte ohne eindeutigen Ortsnamen erscheinen als Kartenbereiche. Gleichnamige Orte lassen sich anhand von Land oder Region unterscheiden.

Auf der Karte soll dieselbe Auswahl über einen gespeicherten Ort, den sichtbaren Ausschnitt oder ein gezeichnetes Rechteck möglich sein. Mehrere Gebiete können später als Vereinigung gewählt werden. Der Zeitraum schränkt diese Vereinigung weiter ein. Ein bekannter Stopp außerhalb der Auswahl unterbricht eine Verbindung. Das gilt auch für Wege über die Datumsgrenze.

## Bearbeitbare Reisevorschläge

1. Ein optional festgelegter Heimatumkreis dient als Bezugspunkt. Ohne ihn beginnen Vorschläge bei zeitlich zusammenhängenden, räumlich konzentrierten Fototagen. Fotos ohne verlässliche Zeit liefern keine Hinweise auf die Reihenfolge.
2. Für einen ersten Prototyp gruppieren wir aufeinanderfolgende Tage mit räumlich zusammenhängenden Stopps. Ein Aufenthalt mit mindestens zwei Aufnahmetagen außerhalb eines Heimatumkreises von beispielsweise 40 km ist ein prüfbarer Ausgangspunkt. Beobachtete Rückkehr, eine längere Aufnahmepause oder ein klarer Ortswechsel geben Hinweise auf das Ende. Die Schwellenwerte werden an unterschiedlichen Testreisen überprüft.
3. Der Vorschlag zeigt Zeitraum, Orte, Anzahl der Fotos und unsichere Übergänge. Der Nutzer kann Tage und Stopps ändern sowie An- und Rückreise getrennt einblenden. Ein Wochenendurlaub, zwei Besuche der gleichen Stadt und eine einzelne Tagestour brauchen eigene Testfälle.
4. Der Export übernimmt den gewählten Reiseausschnitt. Für lange Transfers eignen sich eine Übersichtskarte und separate Tagesblätter. Die lokale Karte wird aus den Stopps des jeweiligen Tages gerahmt. Stadtstopps bleiben dadurch auch bei einer mehrere hundert Kilometer langen Anreise sichtbar.

## Fotoalben und Reisealben

Ein Album ist eine benannte Auswahl von Fotos in **Bilder**. Aus der Bilderliste, der Vollbildgalerie und der Kartenauswahl lassen sich markierte Fotos einem bestehenden Album hinzufügen oder in einem neuen Album sammeln. Ein Foto kann in mehreren Alben erscheinen. Aus einem Album entfernen und aus der Bibliothek löschen bleiben getrennte Aktionen. Titel, Titelbild und Sortierung gehören zum Album.

Der Albumfilter in **Bilder** lässt sich mit Person, Zeitraum und Standort kombinieren. Derselbe Filter kann in **Karte** nur die Standorte dieses Albums zeigen. Aus einer Reise lässt sich ein Reisealbum mit fester Fotoauswahl anlegen. Es speichert die ausgewählten Reisetage, bearbeitete Stopps und die Entscheidungen zur An- und Rückreise, damit sich eine Fotobuchseite später wieder öffnen lässt. Neu importierte Fotos können dem Album gezielt hinzugefügt werden. Ein optionaler gespeicherter Suchfilter, der neue Treffer automatisch aufnimmt, ist ein eigener späterer Schritt.

Als erster Schnitt eignen sich manuelle Alben mit Hinzufügen, Entfernen und Filterung. Danach folgen Reisealben und gespeicherte Fotobuchentwürfe. Die Abnahme prüft überlappende Albumzugehörigkeit, umbenannte oder verschobene Originale, Filterkombinationen und die Wiederherstellung aus einer Sicherung. Für gemeinsam genutzte Bibliotheken bleibt die Freigabe von Alben eine eigene Entscheidung.

## Kartenebenen

Für eine Offline-Satellitenübersicht eignet sich ein kompakter, lokal gebündelter Ausschnitt aus NASA Blue Marble Next Generation. Die Quelle hat eine weltweite Auflösung von 500 Metern pro Pixel. Für eine detaillierte Darstellung im Stadtviertel brauchen wir regionales Bildmaterial oder eine zusätzliche Straßenebene. Sentinel-2 ist eine mögliche Datenquelle für optional installierbare regionale Rasterpakete. Vor der Auswahl prüfen wir die Rechte am konkreten Bildprodukt, die vorgeschriebene Quellenangabe, Größe, Zoomstufen und Drucknutzung. [NASA Blue Marble](https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/) · [NASA-Nutzungshinweise](https://www.nasa.gov/nasa-brand-center/images-and-media/) · [Copernicus-Nutzungsbedingungen](https://dataspace.copernicus.eu/terms-and-conditions)

Eine optionale Online-Ebene braucht eine bewusste Aktivierung, da der Anbieter aus Kachelanfragen den betrachteten Bereich ableiten kann. Fotos und die Stoppliste bleiben lokal. Die Exportvorschau übernimmt die Quellenangabe der gewählten Ebene. Google Maps Tiles hat Beschränkungen für Offline-Nutzung und die Ausfuhr von Karteninhalten. Für Fotobücher planen wir mit Bildmaterial, dessen Lizenz den konkreten Export erlaubt. [Google Maps Tile API Policies](https://developers.google.com/maps/documentation/tile/policies) · [EOxCloudless-Lizenzen](https://cloudless.eox.at/documentation/license)

## Hinweise auf Verkehrsmittel

Wir verwenden ausschließlich verbundene Etappen mit verlässlicher Zeit. Die Differenz zwischen dem letzten Foto am Start und dem ersten Foto am Ziel liefert zusammen mit der Luftlinie eine Untergrenze für die Durchschnittsgeschwindigkeit. Ein kurzer Fußweg oder eine sehr schnelle, lange Strecke kann einen Vorschlag liefern. Bei typischen Überlandgeschwindigkeiten bleibt die Wahl zwischen Auto und Zug offen. Ein Symbol erscheint im Fotobuch nach einer Auswahl durch den Nutzer.

Die Tests enthalten einen Stadtspaziergang in Wien, eine plausible Flugzeit Berlin–Paris, eine mögliche Autofahrt oder Zugfahrt Berlin–München, einen Sprung über die Datumsgrenze sowie fehlende, widersprüchliche und zu weit auseinanderliegende Aufnahmezeiten. Weitere Hinweise wie Flughafennähe kommen erst nach Tests mit Reisen in unterschiedlichen Regionen hinzu.

## Aktivitäten

Die Kopfzeile bietet einen festen Einstieg per Klick. Import, Gruppierung und Bildvorschauen behalten ihre Aktionen. Der Standort- und Zeitabgleich meldet geprüfte Fotos, nicht erreichbare Dateien und den Abschluss. Detailansichten können weiterhin kurz zeigen, warum ihre Daten noch unvollständig sind.

Als Nächstes werden Pfadprüfung, gemeinsamer Bibliotheksabgleich und laufende Update-Downloads mit demselben Statusmodell angezeigt. Filter für laufende, wartende und abgeschlossene Aufgaben sollen erst bei ausreichender Anzahl sichtbar werden. Aktionen bleiben beim zuständigen Auftrag. Diagnostik, Cache-Bereinigung und Timer erscheinen nur, wenn ein Nutzer eingreifen kann. Beim Wiederverbinden zeigt der Server den aktuellen Stand. Nach Wiederherstellung der Bibliothek gehören geographische Fortschrittsdaten zum neuen Bestand. Für Aufgaben ohne belastbare Schätzung entfällt die Restzeit.

## GPU und Anzeige

Der unabhängige Review bestätigt, dass InsightFace 0.7.3 Modellsitzungen auch für später ausgeschlossene ONNX-Dateien startet. Zuerst sollten wir die Initialisierungszeit messen und prüfen, ob nur Gesichtserkennung und Einbettung mit unverändertem Modell und unveränderter Vorverarbeitung geladen werden können. Die Importmessung soll Dekodieren, Inferenz und Vorschauerstellung getrennt erfassen. Beim ersten Import wird die Zahl der Vorbereitungs-Worker bereits gewählt, bevor der tatsächliche Anbieter feststeht.

Die Anzeige unterscheidet Leerlauf, Modellinitialisierung, aktiv geprüften Anbieter und einen Rückfall mit Grund. Eine manuelle Diagnose braucht eine begrenzte Laufzeit. Bei einem blockierenden nativen Aufruf ist eine Prozessgrenze nötig. Vor einer Freigabe prüfen wir den installierten Windows-Build auf NVIDIA-Hardware einschließlich beider produktiven Modelle, CPU-Ersatzpfad und Upgrade. Für die GPU-Verifikation sollten ausgeführte CUDA-Kernel nachweisbar sein.
