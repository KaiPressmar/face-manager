# Änderungsprotokoll

Dieses Protokoll wird in Face Manager angezeigt und beschreibt ausschließlich
Änderungen, die Anwender in der veröffentlichten Anwendung wahrnehmen können.
Technische Entwicklungs-, Test- und Release-Details gehören in Commits und Pull
Requests, nicht in diese Versionshinweise.

## [Unreleased]

### Neu

### Verbessert

### Behoben

## [0.15.0] - 2026-11-25

### Verbessert

- Die bisherige Windows-GPU-Ausgabe kann über das normale App-Update zum gemeinsamen Installer wechseln. Beim Update bleibt die Fotobibliothek erhalten.

### Behoben

- Windows-Netzwerkordner werden beim Einreihen im Hintergrund geprüft. Dadurch bleibt die App während der Ordnerprüfung bedienbar.
- Beim erneuten Import eines Windows-Netzwerkordners erkennt Face Manager unveränderte Fotos zuverlässig wieder.

## [0.14.2] - 2026-11-24

### Neu

- Auf der Projektseite findest du eine bebilderte Tour durch alle Hauptansichten und ein kurzes Bedienvideo.

## [0.14.1] - 2026-11-24

### Neu

- In den Einstellungen lässt sich die Größe des lokalen Vorschau-Speichers ändern oder auf „Ohne Grenze“ setzen.

### Verbessert

- Die Fotoliste bleibt bei langsamen Laufwerken reaktionsfähig. Face Manager erstellt Vorschauen beim Import und zeigt vorhandene Vorschauen auch bei getrennter Quelle an.
- Face Manager räumt ältere Foto- und Gesichtsvorschauen automatisch im Hintergrund auf, damit der lokale Zwischenspeicher begrenzt bleibt.
- Abgebrochene Importe lassen sich aus dem Verlauf wiederholen. Beim Sichern und Wiederherstellen erscheint die Rückmeldung direkt bei der Aktion.

### Behoben

- Fotokacheln öffnen die Galerie per Maus und Touch. „Personen zuordnen“ führt direkt zu den noch unbenannten Gesichtern.
- Eine kurzzeitig getrennte Fotoquelle bleibt als Speicherort in der Bibliothek erhalten.
- Unter Ubuntu lässt sich das App-Fenster schmaler machen. Bei Problemen mit der Ordnerauswahl kannst du den Pfad eingeben.

## [0.14.0] - 2026-11-24

### Neu

- Face Manager lässt sich auf Ubuntu 26.04 als Desktop-App installieren.

### Verbessert

- Ein Windows-Installer deckt Rechner mit und ohne Grafikkarte ab. Unter Windows und Ubuntu nutzt die App bevorzugt NVIDIA, danach eine geeignete Intel-Grafik und bei Bedarf den Prozessor.
- Wer bisher die Windows-GPU-Ausgabe nutzt, kann von der Release-Seite einmalig zum gemeinsamen Installer wechseln.

## [0.13.0] - 2026-11-24

### Neu

- Mehrere Bilderordner lassen sich in einem Schritt auswählen. Falls ein Ordner nicht hinzugefügt werden konnte, kannst du ihn einzeln oder zusammen mit weiteren ausstehenden Ordnern erneut versuchen.

### Verbessert

- Bilder und Personen sind auch auf schmalen Bildschirmen direkt erreichbar. Die Bibliothek zeigt beim Einstieg, wie Fotos gesammelt und Gesichter zugeordnet werden.
- Bei Personenvorschlägen kannst du Vergleichsfotos ansehen, einzelne Gesichter auswählen oder mehrere geprüfte Vorschläge gemeinsam bestätigen. Die Ansicht passt sich auch an schmale Fenster an.
- Vor dem Umbenennen kannst du prüfen, welche Dateien betroffen sind. Anschließend siehst du, welche Änderungen gelungen sind und welche Dateien übersprungen wurden.

### Behoben

- In der Bibliothek erscheinen auch Fotos ohne erkannte Gesichter. Die Originale bleiben an ihren Speicherorten.
- Dateinamen bleiben beim erneuten Prüfen korrekt. Ein ungeeigneter Personenname oder Trenner verschiebt keine Fotos in andere Ordner.
- Auf kleinen Bildschirmen bleibt das Foto sichtbar, auch wenn es viele Kopien an verschiedenen Orten gibt. Auf der Karte lässt sich die Ansicht auch über einer Markierung ziehen.
- Nach einem Import oder einer Bestätigung werden passende Vorschläge weiter vorbereitet. Eine veraltete Vorschlagskarte kann inzwischen hinzugekommene Gesichter nicht mit zuordnen.

## [0.12.3] - 2026-11-24

### Verbessert

- Das Bildraster lädt kleinere Vorschauen beim Scrollen. Das Original wird in der Vollbildansicht geöffnet.
- Die Dateinamenansicht findet Vorschläge auch in großen Bibliotheken schneller.

### Behoben

- Die Dateinamenansicht berücksichtigt den gewählten Ordner auch bei Fotos mit mehreren Speicherorten und findet ältere Vorschläge zuverlässig.
- Die Suche nach Personen findet auch Namen mit Umlauten und anderen Sonderzeichen.
- Änderungen an einer Datei während des Imports lassen sich erneut versuchen. Fehler bei der Erkennung werden angezeigt, und weitere Importe können nach einem Fehler weiterlaufen.
- Nach dem Wechsel der Bibliothek zeigt Face Manager die Gesichtsvorschauen der aktuellen Fotos.
- Dialoge behalten den Tastaturfokus, und die Einstellungen lassen sich nach einem Ladefehler erneut abrufen.
- Auf der Weltkarte lässt sich das Öffnen eines Fotos nach einem Ladefehler wiederholen.
- Bildraster, Dialoge und Importanzeige funktionieren auch mit der älteren Android-12-WebView.
- Der Windows-GPU-Installer enthält die benötigten Dateien für die Beschleunigung. Die Anzeige berücksichtigt, wenn die Erkennung auf den Prozessor zurückfällt.

## [0.12.2] - 2026-11-24

### Behoben

- Eine fehlgeschlagene Wiederherstellung schützt die bisherige Fotobibliothek und behält bei Bedarf eine Kopie zur Wiederherstellung.
- Wenn ein Fotolaufwerk vorübergehend fehlt, bleiben Bilder und Personenzuordnungen in der Bibliothek erhalten.
- Beim Zusammenführen identischer Fotos aus älteren Bibliotheken bleiben vorhandene Gesichtszuordnungen erhalten.
- Die Vollbildgalerie bleibt beim geöffneten Foto, wenn sich die Bibliothek im Hintergrund ändert. Die Fotokarte zeigt neue Änderungen auch bei geöffneter Ansicht an.
- Veraltete Links zu Gesichtsgruppen blockieren die Auswahl nicht mehr. Die Dateinamenliste springt nach dem Umbenennen auf eine gültige Seite, und das Änderungsprotokoll lässt sich nach einem Fehler erneut laden.
- Folgeaufgaben zur Gesichtergruppierung bleiben erhalten, wenn ein abgeschlossener Eintrag ausgeblendet wird.
- Der Windows-Start und fehlgeschlagene Update-Downloads lassen sich zuverlässig wiederholen. Auf Android lässt sich Face Manager nach Abbruch der Installation weiter bedienen.

## [0.12.1] - 2026-11-24

### Verbessert

- Die Fotokarte gruppiert Standorte passend zur Bildschirmgröße, damit die Markierungen auch auf kleinen Bildschirmen einzeln antippbar bleiben.
- Die Fotokarte lässt sich leichter per Tastatur bedienen. Der sichtbare Bereich kann direkt ausgewählt werden und die Fotoliste ist schneller erreichbar.

### Behoben

- Beim Wechsel von Kartenausschnitt oder Zeitraum passen die angezeigten Standorte und Fotos zum gewählten Filter. Auch an der Datumsgrenze öffnen Kartenmarkierungen die richtigen Fotos.
- Beim Nachladen verschieben neu hinzugekommene oder entfernte Fotos die nächste Seite nicht mehr.
- Vorhandene Fotos werden auch dann weiter für die Karte geprüft, wenn die Bibliothek gleichzeitig aktualisiert wird.
- Wenn sich eine Bilddatei nicht öffnen lässt, zeigt die Bildansicht eine Meldung an.
- Alte, länger nicht verwendete Fotovorschauen werden beim Start nach und nach aufgeräumt.
- Erfolgreiche Personenänderungen schließen den Dialog auch dann, wenn die Liste gerade nicht neu geladen werden kann. Lade- und Aktionsfehler bei Dateinamen erscheinen jeweils einmal.

## [0.12.0] - 2026-10-01

### Neu

- Face Manager ist jetzt auch als eigenständige Android-App verfügbar: Fotos und Gesichter direkt auf dem Handy verwalten, die Weltkarte nutzen und dieselben Ansichten und Werkzeuge wie am PC öffnen. Hinweise helfen beim Freigeben von Fotos, Benachrichtigungen und App-Updates.

## [0.11.0] - 2026-10-01

### Neu

- Die neue Weltkarte zeigt Fotos an ihren Aufnahmeorten und hilft, Bilder nach Regionen und Aufnahmezeitraum zu finden.

### Verbessert

- Auch in schmalen Fenstern bleiben die Navigation und die Bedienelemente aller Ansichten erreichbar.
- Dialoge und die Gesichtsauswahl lassen sich leichter mit der Tastatur bedienen; häufige Aktionen benötigen weniger Klicks.

### Behoben

- Bei Ladeproblemen zeigen Bilder und Ordner eine verständliche Fehlermeldung und lassen sich erneut laden, ohne die Auswahl zu verlieren.
- Fehlgeschlagene Änderungen an Personen oder Gruppen bleiben zur Korrektur geöffnet und zeigen den Grund an.
- Beim wiederholten Öffnen von Ansichten werden kürzlich geladene Inhalte seltener unnötig neu geladen.

## [0.10.2] - 2026-07-22

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
