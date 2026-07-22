# Änderungsprotokoll

Dieses Protokoll wird in Face Manager angezeigt und beschreibt ausschließlich
Änderungen, die Anwender in der veröffentlichten Anwendung wahrnehmen können.
Technische Entwicklungs-, Test- und Release-Details gehören in Commits und Pull
Requests, nicht in diese Versionshinweise.

## [Unreleased]

### Neu

### Verbessert

- Die Restzeit großer Bilderimporte wird ruhiger und verlässlicher angezeigt und springt bei einzelnen schnellen oder langsamen Bildern nicht mehr ständig hin und her.

### Behoben

- Die Neuordnung sehr großer Bildbestände bricht bei vielen noch nicht zugeordneten Gesichtern nicht mehr vorzeitig ab.
- Sehr lange Windows-Pfade und leicht unvollständige Bilddateien werden beim Import zuverlässiger verarbeitet; Dateien, die tatsächlich keine Bilder sind, werden sicher übersprungen.

## [0.10.1] - 2026-07-22

### Behoben

- Nach einem Update zeigen Desktop und Startmenü zuverlässig das aktuelle einheitliche Anwendungssymbol statt einer alten zwischengespeicherten Version.

## [0.10.0] - 2026-07-22

### Neu

- Nicht mehr vorhandene Bilddateien werden im Leerlauf bereinigt; zusätzlich lässt sich die Prüfung unter „Daten und Wartung“ jederzeit manuell starten.

### Verbessert

- Große Bilderordner werden schneller importiert; bereits bekannte Dateien werden zügiger übersprungen und Vorschaubilder bremsen den Import nicht mehr aus.

### Behoben

- Die Ordnerfilterung berücksichtigt unter Windows wieder zuverlässig alle Unterordner, auch bei Leerzeichen und Sonderzeichen im Pfad.
- Liefert ein Bilderfilter keine Treffer, bleibt die Bibliothek mit einer Möglichkeit zum Zurücksetzen sichtbar, statt fälschlich zum ersten Import aufzufordern.
- Bilddateien lassen sich aus Face Manager zuverlässig im Windows-Explorer öffnen und markieren, auch bei Netzwerkpfaden, Leerzeichen und Sonderzeichen.

## [0.9.1] - 2026-07-22

### Verbessert

- Die Steuerung laufender Aktivitäten ist mit kompakten, klar erkennbaren Symbolen übersichtlicher und platzsparender.
- Die geschätzte Restzeit großer Importe passt sich stabiler an und wird durch einzelne langsame Bilder oder Pausen weniger verzerrt.
- Beim Hinzufügen von Bilderordnern zeigt die installierte Anwendung nur noch die passende Windows-Ordnerauswahl; die manuelle Pfadeingabe bleibt gezielt der Entwicklungsumgebung vorbehalten.

## [0.9.0] - 2026-07-22

### Neu

- Laufende Importe, Neuordnungen und Vorschauarbeiten lassen sich pausieren, fortsetzen oder abbrechen; beendete Aufgaben können einzeln oder vollständig aus der Historie gelöscht werden.

### Behoben

- Während laufender Importe bleiben alle Ansichten reaktionsfähig; neue Inhalte werden ruhig gebündelt und unterbrechen weder Scrollposition noch Auswahl.

## [0.8.0] - 2026-07-22

### Neu

- Bilder und Gesichtsgruppen aktualisieren sich während laufender Importe und Neu-Gruppierungen automatisch, ohne die aktuelle Arbeitsposition zu verändern.
- In der Bilderansicht lässt sich die Größe der Bilder im Raster jetzt zwischen sehr klein, klein, mittel und groß wählen; die Auswahl bleibt für den nächsten Start erhalten.

### Verbessert

- Zuordnungen lassen sich während eines Bildimports sicher weiterbearbeiten; manuelle Änderungen behalten Vorrang und neue Gesichter werden zuverlässig einsortiert.
- Filter, Sortierung und Darstellungsoptionen sind in den Bilder- und Dateiansichten übersichtlicher angeordnet und bleiben auch auf schmalen Fenstern gut bedienbar.

### Behoben

- Der Sprung von einem Bild zu einer Gesichtsgruppe zeigt jetzt bereits beim ersten Versuch zuverlässig die ausgewählte Gruppe und ihre Gesichter.

## [0.7.1] - 2026-07-22

### Verbessert

- Face Manager hat ein neues, klareres App-Icon, das im Programmfenster, im Browser-Tab und in der Seitenleiste einheitlich erscheint.

## [0.7.0] - 2026-07-22

### Verbessert

- Nach einem Update zeigt Face Manager jetzt die Neuerungen aller übersprungenen Versionen an und nicht mehr nur der neuesten. Das vollständige Änderungsprotokoll lässt sich jederzeit über die Versionsnummer öffnen.
- Während laufender Importe reagiert die Oberfläche flüssiger, und Gesichts-Vorschauen stehen nach Importen und Neu-Gruppierungen schneller bereit.

### Behoben

- Ein von Hand gestartetes Neu-Ordnen der Gesichtsgruppen wird jetzt auch während eines laufenden Bildimports zuverlässig übernommen und beginnt automatisch, sobald der Import abgeschlossen ist. Face Manager zeigt dabei an, dass die Aufgabe eingeplant ist.

## [0.6.1] - 2026-07-20

### Behoben

- Windows-Installationsdateien werden für neue Versionen wieder vollständig zum Download bereitgestellt.

## [0.6.0] - 2026-07-20

### Neu

- Eine neue Prüfansicht bündelt unsichere Gesichter, mögliche Fehl-Erkennungen und Vorschläge für bereits bekannte Personen an einem Ort.
- Bilder und Umbenennungen lassen sich jetzt flexibel nach mehreren Personen, Ordnern und Sortierungen filtern.
- Die Oberfläche unterstützt helle, dunkle und automatisch vom Betriebssystem übernommene Darstellung.
- Datenbank-Sicherungen können direkt in den Einstellungen exportiert und wieder eingespielt werden.
- Nach einem Update zeigt Face Manager die wichtigsten Neuerungen beim ersten Start an; über die Versionsnummer bleiben sie später erreichbar.
- Face Manager prüft stündlich auf neue GitHub-Versionen und kann den passenden Windows-Installer nach einer Sicherheitsprüfung herunterladen und auf ausdrücklichen Wunsch starten.

### Verbessert

- In der Vollbildansicht lässt sich direkt durch alle Gesichter der geöffneten Gruppe navigieren; dabei werden die zugehörige Person oder Kategorie, die konkrete Gesichtsgruppe und ausschließlich der Rahmen des aktuellen Crops angezeigt, ohne dort versehentlich ganze Bilder entfernen zu können.
- Dateipfade lassen sich in Bild-, Vollbild- und Umbenennungsansichten eindeutig kopieren oder im Explorer beziehungsweise Dateimanager öffnen; bei mehreren Speicherorten bleibt jeder Pfad separat auswählbar.
- Navigation, Seitenüberschriften, Einstellungen und Dialoge sind einheitlich benannt und leicht verständlich formuliert. Einheitliche Boxen, Hervorhebungen und Schatten machen Inhalte und Handlungsebenen schneller erfassbar.
- Seiten und Einstellungsbereiche besitzen einheitliche, aktualisierungssichere Adressen. Dadurch bleiben die aktuelle Ansicht sowie Zurück- und Vorwärtsnavigation im Browser und in der installierten Anwendung zuverlässig erhalten.
- Die Gesichtserkennung gruppiert vorsichtiger, berücksichtigt bestätigte Personen und kann ihre Empfindlichkeit anhand vorhandener Zuordnungen abstimmen.
- Die Kopfleiste ist platzsparend nach Bildimport, Aktivitäten und Darstellung gegliedert; Versionsinformationen und verfügbare Updates sind dezent in den Markenbereich der Sidebar integriert. Einheitliche Größen und Darstellungen sorgen für ein ruhigeres Gesamtbild. Hintergrundaufgaben bleiben dezent sichtbar, ihre verständlichen Einzelheiten und kürzlich abgeschlossene Arbeiten sind ohne Klick per Maus erreichbar. Parallele Bildimporte werden in der gemeinsamen Zeitprognose korrekt berücksichtigt.
- Die Dateinamenseite konzentriert sich auf Auswahl und Umbenennung und führt bei Bedarf direkt zum Benennungsschema. Dessen Regeln für den Personen-Anhang und mehrere erkannte Personen sind klar getrennt und werden gemeinsam in einer verständlichen Vorschau gezeigt.
- Gesichts-Vorschaubilder werden im Hintergrund vorbereitet, wodurch Prüfansichten und die Navigation in großen Bibliotheken schneller reagieren.
- Die Windows-Installer enthalten konsistente Versions- und Projektangaben sowie überprüfbare Prüfsummen und Herkunftsnachweise.

### Behoben

- In „Personen korrigieren“ ist die Bearbeitung des Personennamens sofort erreichbar und nicht mehr vom vorherigen Öffnen der Gruppen-Umbenennung abhängig.
- Zuordnungen und Cluster bleiben auch bei parallelen Importen, späteren Korrekturen und automatischen Neu-Gruppierungen konsistent.
- Doppelte, verschobene oder ersetzte Bilddateien sowie ältere Datenbankstände werden zuverlässiger erkannt und repariert.
- Hintergrundarbeiten geben manuellen Änderungen Vorrang und blockieren die Oberfläche nicht länger als nötig.
