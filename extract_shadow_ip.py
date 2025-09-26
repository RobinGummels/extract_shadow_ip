from typing import Any, Optional

from PyQt5.QtCore import QCoreApplication, QVariant

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsWkbTypes,
    QgsField,
    QgsVectorLayer,
    QgsPointXY,
)
from qgis import processing


class extract_shadow_ip(QgsProcessingAlgorithm):
    """
    Extract shadow IP from buildings and ZB-WEA points.
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    # Requiered inputs:
    # Input buildings (vector polygons)
    # Input ZB-WEA (vector points)
    # Output IP (vector points)

    input_buildings = "input_buildings"
    input_zb_wea = "input_zb_wea"
    output_ip = "output_ip"

    def initAlgorithm(self, config: Optional[dict[str, Any]] = None):
        """
        Create GUI
        """

        # Building-Polygons Input
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.input_buildings,
                self.tr("Building-Polygons"),
                types=[QgsProcessing.TypeVectorPolygon],
            )
        )

        # ZB-WEA-Points Input
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.input_zb_wea,
                self.tr("ZB-WEA-Points"),
                types=[QgsProcessing.TypeVectorPoint],
            )
        )

        # Threshold für Voronoi-Ausdünnung
        self.addParameter(
            QgsProcessingParameterNumber(
                "area_threshold",
                self.tr("Area threshold for Voronoi thinning (m²)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2500.0,
                minValue=1.0,
            )
        )

        # Mindestabstand zwischen IP
        self.addParameter(
            QgsProcessingParameterNumber(
                "min_distance",
                self.tr("Minimum distance between IP points (m)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=20.0,
                minValue=0.0,
            )
        )

        # Output layers für verschiedene Importance-Klassen (finale ausgedünnte Punkte)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_final_importance_3",
                "Final: Definitiv benötigt Gebäude (nach Thinning)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_final_importance_2",
                "Final: Weitere Prüfung erforderlich (nach Thinning)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_final_importance_0", "Final: Zuordnungsfehler (nach Thinning)"
            )
        )

        # Output layer für alle ursprünglich extrahierten Punkte (vor Thinning)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_all_extracted", "Alle extrahierten Punkte (vor Thinning)"
            )
        )

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        """
        Main processing function that coordinates all algorithm steps.

        Progress distribution:
        - Building classification: 25%
        - Point extraction: 25%
        - Distance thinning: 10%
        - Voronoi thinning: 30%
        - Writing results: 10%
        """

        # Retrieve and validate inputs
        input_buildings, input_zb_wea, area_threshold, min_distance = (
            self._validate_inputs(parameters, context)
        )

        # Setup output sinks
        (
            final_sinks,
            final_dest_ids,
            all_extracted_sink,
            all_extracted_dest_id,
            fields,
        ) = self._setup_output_sinks(parameters, context, input_buildings)

        # Get building features
        building_features = list(input_buildings.getFeatures())

        # Step 1: Classify buildings (0-25%)
        feedback.pushInfo("Starting building classification...")
        importance_list = self._classify_buildings(
            building_features, feedback, progress_start=0, progress_end=25
        )

        # Step 2: Extract shadow points (25-50%)
        feedback.pushInfo("Extracting shadow points...")
        all_created_features = self._extract_shadow_points(
            building_features,
            input_zb_wea,
            importance_list,
            fields,
            feedback,
            progress_start=25,
            progress_end=50,
        )

        # Step 3: Distance-based thinning (50-60%)
        feedback.pushInfo("Starting distance-based thinning...")
        distance_thinned_features = self._perform_distance_thinning(
            all_created_features,
            min_distance,
            feedback,
            progress_start=50,
            progress_end=60,
        )

        # Step 4: Voronoi thinning in multiple passes (60-90%)
        feedback.pushInfo("Starting Voronoi thinning...")

        # Pass 1: Remove importance class 2 points (60-70%)
        thinned_features = self._perform_voronoi_thinning(
            distance_thinned_features,
            area_threshold,
            2,
            input_buildings.sourceCrs(),
            context,
            feedback,
            progress_start=60,
            progress_end=70,
        )

        # Pass 2: Remove importance class 0 points (70-80%)
        thinned_features = self._perform_voronoi_thinning(
            thinned_features,
            area_threshold,
            0,
            input_buildings.sourceCrs(),
            context,
            feedback,
            progress_start=70,
            progress_end=80,
        )

        # Pass 3: Remove importance class 3 points (80-90%)
        thinned_features = self._perform_voronoi_thinning(
            thinned_features,
            area_threshold,
            3,
            input_buildings.sourceCrs(),
            context,
            feedback,
            progress_start=80,
            progress_end=90,
        )

        # Step 5: Write final results (90-100%)
        feedback.pushInfo("Writing final results...")
        self._write_final_results(
            all_created_features,
            thinned_features,
            final_sinks,
            all_extracted_sink,
            feedback,
            progress_start=90,
            progress_end=100,
        )

        return {
            "output_final_importance_0": final_dest_ids[0],
            "output_final_importance_2": final_dest_ids[2],
            "output_final_importance_3": final_dest_ids[3],
            "output_all_extracted": all_extracted_dest_id,
        }

    def _validate_inputs(self, parameters, context):
        """
        Validate and retrieve input parameters.

        Returns:
            tuple: (input_buildings, input_zb_wea, area_threshold, min_distance)
        """
        # Retrieve the inputs
        input_buildings = self.parameterAsSource(
            parameters, self.input_buildings, context
        )
        input_zb_wea = self.parameterAsSource(parameters, self.input_zb_wea, context)
        area_threshold = self.parameterAsDouble(parameters, "area_threshold", context)
        min_distance = self.parameterAsDouble(parameters, "min_distance", context)

        # If input was not found, throw an exception
        if input_buildings is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.input_buildings)
            )

        if input_zb_wea is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.input_zb_wea)
            )

        return input_buildings, input_zb_wea, area_threshold, min_distance

    def _setup_output_sinks(self, parameters, context, input_buildings):
        """
        Setup output feature sinks for each importance class.

        Returns:
            tuple: (sinks, dest_ids, fields)
        """
        # Felder kopieren und 'importance' hinzufügen
        fields = input_buildings.fields()
        fields.append(QgsField("importance", QVariant.Int))

        # Create sinks for final importance classes (after thinning)
        final_sinks = {}
        final_dest_ids = {}

        for importance in [0, 2, 3]:
            (sink, dest_id) = self.parameterAsSink(
                parameters,
                f"output_final_importance_{importance}",
                context,
                fields,  # mit importance-Feld
                QgsWkbTypes.Point,
                input_buildings.sourceCrs(),
            )
            final_sinks[importance] = sink
            final_dest_ids[importance] = dest_id

        # Create sink for all extracted points (before thinning)
        (all_extracted_sink, all_extracted_dest_id) = self.parameterAsSink(
            parameters,
            "output_all_extracted",
            context,
            fields,  # mit importance-Feld
            QgsWkbTypes.Point,
            input_buildings.sourceCrs(),
        )

        # If sink was not created, throw an exception
        if (
            any(sink is None for sink in final_sinks.values())
            or all_extracted_sink is None
        ):
            raise QgsProcessingException(
                self.invalidSinkError(parameters, "output_final_importance_0")
            )

        return (
            final_sinks,
            final_dest_ids,
            all_extracted_sink,
            all_extracted_dest_id,
            fields,
        )

    def _get_building_classification_codes(self):
        """
        Get the classification codes for building importance assessment.

        Returns:
            tuple: (yes_codes, no_codes, maybe_codes)
        """
        yes_codes = [
            1000,
            1010,
            1020,
            1021,
            1022,
            1023,
            1024,
            1025,
            1100,
            1110,
            1120,
            1121,
            1122,
            1123,
            1130,
            1131,
            1210,
            1220,
            1221,
            1222,
            1223,
            1311,
            1312,
            2020,
            2030,
            2040,
            2054,
            2055,
            2056,
            2070,
            2071,
            2072,
            2073,
            2074,
            2080,
            2081,
            2082,
            2083,
            2090,
            2130,
            2180,
            2310,
            2320,
            2411,
            2421,
            3000,
            3010,
            3011,
            3012,
            3013,
            3014,
            3015,
            3016,
            3017,
            3018,
            3019,
            3020,
            3021,
            3022,
            3023,
            3024,
            3048,
            3050,
            3051,
            3052,
            3053,
            3054,
            3060,
            3061,
            3062,
            3063,
            3064,
            3065,
            3066,
            3070,
            3071,
            3072,
            3073,
            3075,
            3100,
            3200,
            3240,
            3241,
            3242,
        ]
        no_codes = [
            1313,
            2060,
            2091,
            2092,
            2093,
            2094,
            2095,
            2100,
            2110,
            2111,
            2112,
            2113,
            2114,
            2120,
            2121,
            2131,
            2140,
            2141,
            2142,
            2143,
            2150,
            2160,
            2170,
            2171,
            2172,
            2200,
            2210,
            2211,
            2212,
            2213,
            2220,
            2422,
            2431,
            2442,
            2444,
            2460,
            2461,
            2462,
            2463,
            2464,
            2465,
            2500,
            2501,
            2510,
            2511,
            2512,
            2513,
            2520,
            2521,
            2522,
            2523,
            2527,
            2528,
            2529,
            2540,
            2560,
            2570,
            2571,
            2580,
            2590,
            2591,
            2600,
            2610,
            2611,
            2612,
            2620,
            2621,
            2622,
            2623,
            2700,
            2720,
            2721,
            2723,
            2724,
            2726,
            2727,
            2728,
            2729,
            2732,
            2735,
            2740,
            2741,
            2742,
            3030,
            3031,
            3032,
            3033,
            3034,
            3035,
            3036,
            3037,
            3038,
            3040,
            3041,
            3042,
            3043,
            3044,
            3045,
            3046,
            3047,
            3074,
            3080,
            3081,
            3082,
            3210,
            3211,
            3212,
            3220,
            3221,
            3222,
            3230,
            3262,
            3264,
            3272,
            3281,
            1250,
            1400,
            1201,
            1205,
            1610,
            1611,
            1750,
        ]
        maybe_codes = [
            1310,
            2000,
            2010,
            2050,
            2051,
            2052,
            2053,
            2400,
            2410,
            2412,
            2420,
            2423,
            2424,
            2430,
            2440,
            2441,
            2443,
            2450,
            2451,
            3090,
            3091,
            3092,
            3094,
            3095,
            3097,
            3098,
            3260,
            3261,
            3263,
            3270,
            3271,
            3273,
            3280,
            3290,
            9998,
        ]

        return yes_codes, no_codes, maybe_codes

    def _classify_buildings(
        self, building_features, feedback, progress_start, progress_end
    ):
        """
        Classify buildings based on their gfkzshh attribute and neighborhood relationships.

        Args:
            building_features: List of building features
            feedback: Processing feedback object
            progress_start: Start percentage for progress bar
            progress_end: End percentage for progress bar

        Returns:
            list: Importance values for each building
        """
        feedback.pushInfo("Adding building type categories")
        feedback.pushInfo("3 = definitly needed")
        feedback.pushInfo("2 = maybe needed")
        feedback.pushInfo("1 = definitely not needed")
        feedback.pushInfo("0 = unknown (gfkzshh missing/could'nt be matched)")

        # Get classification codes
        yes_codes, no_codes, maybe_codes = self._get_building_classification_codes()

        # Add extra parameter to each input_building-Feature.
        # Based on the attribute "gebnutzbez", a new attribute should be calculated
        # Berechne importance für jedes Gebäude und speichere in Liste
        importance_list = []
        progress_range = progress_end - progress_start

        for current, building_feature in enumerate(building_features):
            gfkzshh = (
                str(building_feature.attribute("gfkzshh"))
                if building_feature.attribute("gfkzshh") is not None
                else ""
            )
            importance = 0  # Fallback if gfkzshh is missing

            # Erste Zuordnung basierend auf gfkzshh
            if importance == 0:
                for code in yes_codes:
                    if gfkzshh.endswith(str(code)):
                        importance = 3
                        break
            if importance == 0:
                for code in no_codes:
                    if gfkzshh.endswith(str(code)):
                        importance = 1
                        break
            if importance == 0:
                for code in maybe_codes:
                    if gfkzshh.endswith(str(code)):
                        importance = 2
                        break

            # Wenn das Nachbargebäude importance 3 hat und das eigene Gebäude < 3, dann bekommt dieses Gebäude eine 1
            if importance < 3:
                for other_feature in building_features:
                    if building_feature.geometry().intersects(other_feature.geometry()):
                        if other_feature != building_feature:
                            other_gfkzshh = (
                                str(other_feature.attribute("gfkzshh"))
                                if building_feature.attribute("gfkzshh") is not None
                                else ""
                            )
                            for code in yes_codes:
                                if other_gfkzshh.endswith(str(code)):
                                    importance = 1
                                    break
            # Füge den berechneten Wert der Liste hinzu
            importance_list.append(importance)

            # Update progress
            if len(building_features) > 0:
                progress = (
                    progress_start + (current / len(building_features)) * progress_range
                )
                feedback.setProgress(int(progress))

        return importance_list

    def _extract_shadow_points(
        self,
        building_features,
        input_zb_wea,
        importance_list,
        fields,
        feedback,
        progress_start,
        progress_end,
    ):
        """
        Extract shadow points from building edges that are closest to ZB-WEA points.

        Args:
            building_features: List of building features
            input_zb_wea: ZB-WEA point source
            importance_list: List of importance values for buildings
            fields: Output field structure
            feedback: Processing feedback object
            progress_start: Start percentage for progress bar
            progress_end: End percentage for progress bar

        Returns:
            list: Created features with shadow points
        """
        feedback.pushInfo("Extracting nearest point to WEA")

        # Sammle alle Features für spätere Voronoi-Berechnung
        all_created_features = []
        progress_range = progress_end - progress_start

        for current, building_feature in enumerate(building_features):
            # Create point on every line in the polygon
            points = []
            geom = building_feature.geometry()
            if geom.isMultipart():
                for part in geom.asMultiPolygon():
                    for ring in part:  # ring ist eine Liste von QgsPointXY
                        for i in range(len(ring) - 1):
                            line = QgsGeometry.fromPolylineXY([ring[i], ring[i + 1]])
                            # Hausseiten kürzer als 2m nicht berücksichtigen
                            if line.length() > 2:
                                midpoint = line.interpolate(line.length() / 2)
                                points.append(midpoint)
            else:
                for ring in geom.asPolygon():
                    for i in range(len(ring) - 1):
                        line = QgsGeometry.fromPolylineXY([ring[i], ring[i + 1]])
                        # Hausseiten kürzer als 2m nicht berücksichtigen
                        if line.length() > 2:
                            midpoint = line.interpolate(line.length() / 2)
                            points.append(midpoint)

            # Check which points is the closest to the points of input_zb_wea
            # Only add the point with the closest distance to any input_zb_wea feature to the feature sink
            closest_point = None
            closest_distance = float("inf")
            for point in points:
                for zb_feature in input_zb_wea.getFeatures():
                    distance = zb_feature.geometry().distance(point)
                    if distance < closest_distance:
                        closest_distance = distance
                        closest_point = point
            if closest_point:
                feature = QgsFeature()
                feature.setGeometry(closest_point)
                importance = importance_list[
                    current
                ]  # Verwende den berechneten Wert aus der Liste
                attrs = building_feature.attributes() + [importance]
                feature.setAttributes(attrs)

                # Speichere Feature für Voronoi-Berechnung
                all_created_features.append(feature)

            # Update progress
            if len(building_features) > 0:
                progress = (
                    progress_start + (current / len(building_features)) * progress_range
                )
                feedback.setProgress(int(progress))

        return all_created_features

    def _perform_distance_thinning(
        self, all_created_features, min_distance, feedback, progress_start, progress_end
    ):
        """
        Perform distance-based thinning of shadow points with hierarchical rules.

        Rules:
        1. Remove importance 2 points that are too close to importance 0 or 3 points
        2. Remove importance 0 points that are too close to importance 3 points

        Args:
            all_created_features: List of all created features
            min_distance: Minimum distance threshold in meters
            feedback: Processing feedback object
            progress_start: Start percentage for progress bar
            progress_end: End percentage for progress bar

        Returns:
            list: Features after distance-based thinning
        """
        if min_distance <= 0:
            feedback.pushInfo("Distance thinning disabled (min_distance <= 0)")
            return all_created_features

        feedback.pushInfo(
            f"Starting distance-based thinning with min_distance: {min_distance}m"
        )

        # Separiere Features nach Importance-Klassen
        features_by_importance = {0: [], 2: [], 3: []}
        other_features = []

        for feature in all_created_features:
            importance = feature.attributes()[-1]  # Importance ist das letzte Attribut
            if importance in features_by_importance:
                features_by_importance[importance].append(feature)
            else:
                other_features.append(feature)

        feedback.pushInfo(
            f"Features by importance: 0={len(features_by_importance[0])}, 2={len(features_by_importance[2])}, 3={len(features_by_importance[3])}"
        )

        # Führe hierarchische Thinning-Regeln aus
        features_to_keep = []

        # Regel 1: Prüfe importance 2 gegen importance 0 und 3
        feedback.pushInfo(
            "Rule 1: Checking importance 2 points against importance 0 and 3..."
        )
        feedback.setProgress(
            int(progress_start + (progress_end - progress_start) * 0.3)
        )

        for feature_2 in features_by_importance[2]:
            point_2 = feature_2.geometry().asPoint()
            keep_feature = True

            # Prüfe gegen importance 0 Punkte
            for feature_0 in features_by_importance[0]:
                point_0 = feature_0.geometry().asPoint()
                distance = point_2.distance(point_0)
                if distance < min_distance:
                    keep_feature = False
                    break

            # Prüfe gegen importance 3 Punkte (nur wenn noch nicht entfernt)
            if keep_feature:
                for feature_3 in features_by_importance[3]:
                    point_3 = feature_3.geometry().asPoint()
                    distance = point_2.distance(point_3)
                    if distance < min_distance:
                        keep_feature = False
                        break

            if keep_feature:
                features_to_keep.append(feature_2)

        feedback.pushInfo(
            f"After rule 1: {len(features_to_keep)} importance 2 points kept (removed {len(features_by_importance[2]) - len(features_to_keep)})"
        )

        # Regel 2: Prüfe importance 0 gegen importance 3
        feedback.pushInfo(
            "Rule 2: Checking importance 0 points against importance 3..."
        )
        feedback.setProgress(
            int(progress_start + (progress_end - progress_start) * 0.6)
        )

        kept_importance_0 = []
        for feature_0 in features_by_importance[0]:
            point_0 = feature_0.geometry().asPoint()
            keep_feature = True

            # Prüfe gegen importance 3 Punkte
            for feature_3 in features_by_importance[3]:
                point_3 = feature_3.geometry().asPoint()
                distance = point_0.distance(point_3)
                if distance < min_distance:
                    keep_feature = False
                    break

            if keep_feature:
                kept_importance_0.append(feature_0)

        feedback.pushInfo(
            f"After rule 2: {len(kept_importance_0)} importance 0 points kept (removed {len(features_by_importance[0]) - len(kept_importance_0)})"
        )

        # Sammle alle behaltenen Features
        final_features = []
        final_features.extend(kept_importance_0)  # Gefilterte importance 0
        final_features.extend(features_to_keep)  # Gefilterte importance 2
        final_features.extend(
            features_by_importance[3]
        )  # Alle importance 3 (werden nie entfernt)
        final_features.extend(other_features)  # Andere Features (importance 1 etc.)

        feedback.setProgress(int(progress_end))
        feedback.pushInfo(
            f"Distance thinning completed. Features: {len(all_created_features)} -> {len(final_features)} (removed {len(all_created_features) - len(final_features)})"
        )

        return final_features

    def _perform_voronoi_thinning(
        self,
        all_created_features,
        area_threshold,
        target_importance_class,
        source_crs,
        context,
        feedback,
        progress_start,
        progress_end,
    ):
        """
        Perform Voronoi-based thinning of shadow points for a specific importance class.

        Args:
            all_created_features: List of all created features
            area_threshold: Minimum area threshold for polygons
            target_importance_class: Only points of this importance class can be deleted (0, 2, or 3)
            source_crs: Source coordinate reference system
            context: Processing context
            feedback: Processing feedback object
            progress_start: Start percentage for progress bar
            progress_end: End percentage for progress bar

        Returns:
            list: Features after thinning (with some features potentially removed)
        """
        feedback.pushInfo(
            f"Starting Voronoi thinning process for importance class {target_importance_class}..."
        )

        # Sammle ALLE Features für Voronoi-Berechnung (aber nur target_importance_class kann gelöscht werden)
        voronoi_points = []
        point_importance = []
        point_features = []

        for feature in all_created_features:
            importance = feature.attributes()[-1]  # Importance ist das letzte Attribut

            # Alle Punkte mit Importance 0, 2, 3 für Voronoi verwenden (für korrekten Kontext)
            if importance in [0, 2, 3]:
                voronoi_points.append(feature.geometry().asPoint())
                point_importance.append(importance)
                point_features.append(feature)

        if not voronoi_points:
            feedback.pushInfo("No points found for Voronoi calculation")
            return []

        iteration = 0
        max_iterations = 50
        progress_range = progress_end - progress_start

        while iteration < max_iterations:
            iteration += 1
            feedback.pushInfo(
                f"Voronoi iteration {iteration}, points: {len(voronoi_points)}"
            )

            # Update progress
            progress = progress_start + (iteration / max_iterations) * progress_range
            feedback.setProgress(int(progress))

            if len(voronoi_points) < 3:
                break

            # Berechne Voronoi-Polygone mit QGIS Processing
            try:
                # Erstelle temporären Layer mit aktuellen Punkten
                temp_layer = QgsVectorLayer(
                    "Point?crs=" + source_crs.authid(), "temp_points", "memory"
                )
                temp_provider = temp_layer.dataProvider()
                temp_provider.addAttributes(
                    [
                        QgsField("importance", QVariant.Int),
                        QgsField("point_id", QVariant.Int),
                    ]
                )
                temp_layer.updateFields()

                # Füge Features batch-weise hinzu für bessere Performance
                features_to_add = []
                for i, point in enumerate(voronoi_points):
                    if point is None or not point.x() or not point.y():
                        continue
                    feat = QgsFeature()
                    feat.setGeometry(QgsGeometry.fromPointXY(point))
                    feat.setAttributes([point_importance[i], i])
                    features_to_add.append(feat)

                if not features_to_add:
                    feedback.pushInfo("No valid points for Voronoi calculation")
                    break

                temp_provider.addFeatures(features_to_add)

                # Berechne Voronoi mit 10% Puffer
                extent = temp_layer.extent()
                if extent.isEmpty() or extent.width() == 0 or extent.height() == 0:
                    feedback.pushInfo("Invalid extent for Voronoi calculation")
                    break

                buffer_size = max(extent.width(), extent.height()) * 0.1

                voronoi_result = processing.run(
                    "qgis:voronoipolygons",
                    {"INPUT": temp_layer, "BUFFER": buffer_size, "OUTPUT": "memory:"},
                    context=context,
                    feedback=feedback,
                )

                voronoi_layer = voronoi_result["OUTPUT"]

                # Prüfe ob Voronoi-Layer gültig ist
                if not voronoi_layer or voronoi_layer.featureCount() == 0:
                    feedback.pushInfo("No Voronoi polygons generated")
                    break

                # Sammle Polygone unter dem Threshold
                to_delete = []
                voronoi_polygons = {}

                for feature in voronoi_layer.getFeatures():
                    if not feature.geometry() or feature.geometry().isEmpty():
                        continue

                    area = feature.geometry().area()
                    point_id = feature.attribute("point_id")

                    # Verwende point_id für sicheren Index-Zugriff
                    if point_id is not None and 0 <= point_id < len(voronoi_points):
                        point_index = point_id
                        importance = point_importance[point_index]

                        # Erstelle Kopie der Geometrie für sichere Speicherung
                        geom_copy = QgsGeometry(feature.geometry())
                        voronoi_polygons[point_index] = geom_copy

                        if (
                            area < area_threshold
                            and importance == target_importance_class
                        ):
                            to_delete.append((point_index, importance, area))

                if not to_delete:
                    feedback.pushInfo(
                        f"No more polygons below threshold found for importance class {target_importance_class}"
                    )
                    feedback.pushInfo("Stopping iterations - no polygons to delete")
                    break

                feedback.pushInfo(
                    f"Found {len(to_delete)} polygons below threshold for importance class {target_importance_class}"
                )

                # Sortiere nach Fläche (kleinste zuerst)
                to_delete.sort(key=lambda x: x[2])

                # Sammle Punkte zum Löschen (keine benachbarten)
                delete_indices = []

                for point_idx, importance, area in to_delete:
                    if point_idx in delete_indices:
                        continue

                    # Prüfe ob bereits ein benachbarter Punkt zum Löschen markiert ist
                    current_polygon = voronoi_polygons.get(point_idx)
                    if current_polygon is None:
                        continue

                    can_delete = True
                    for existing_idx in delete_indices:
                        existing_polygon = voronoi_polygons.get(existing_idx)
                        if existing_polygon and current_polygon.intersects(
                            existing_polygon
                        ):
                            can_delete = False
                            break

                    if can_delete:
                        delete_indices.append(point_idx)

                if not delete_indices:
                    feedback.pushInfo(
                        "No points can be deleted (all would create adjacent deletions)"
                    )
                    feedback.pushInfo("Stopping iterations - no progress possible")
                    break

                feedback.pushInfo(f"Deleting {len(delete_indices)} points")

                # Entferne die markierten Punkte - erstelle neue Listen statt pop()
                new_voronoi_points = []
                new_point_importance = []
                new_point_features = []

                for i in range(len(voronoi_points)):
                    if i not in delete_indices:
                        new_voronoi_points.append(voronoi_points[i])
                        new_point_importance.append(point_importance[i])
                        new_point_features.append(point_features[i])

                voronoi_points = new_voronoi_points
                point_importance = new_point_importance
                point_features = new_point_features

                # Explizite Garbage Collection
                import gc

                gc.collect()

            except Exception as e:
                feedback.pushInfo(f"Error in Voronoi calculation: {str(e)}")
                import traceback

                feedback.pushInfo(f"Traceback: {traceback.format_exc()}")
                break

        feedback.pushInfo(
            f"Voronoi thinning completed after {iteration} iterations for importance class {target_importance_class}"
        )
        feedback.pushInfo(f"Final point count: {len(voronoi_points)}")

        # Erstelle neue Liste mit allen ursprünglichen Features
        # Entferne nur die Features, die in voronoi_points nicht mehr enthalten sind
        remaining_voronoi_features = set()
        for i, point in enumerate(voronoi_points):
            original_feature = point_features[i]
            # Erstelle eindeutige ID für Vergleich
            geom_wkt = original_feature.geometry().asWkt()
            attrs_key = str(original_feature.attributes())
            feature_key = f"{geom_wkt}_{attrs_key}"
            remaining_voronoi_features.add(feature_key)

        # Filtere all_created_features: behalte alle außer den gelöschten
        thinned_features = []
        for feature in all_created_features:
            importance = feature.attributes()[-1]

            if importance == 1:
                # Importance 1 Features werden nie ausgedünnt
                thinned_features.append(feature)
            elif importance in [0, 2, 3]:
                # Prüfe ob Feature in den verbleibenden Voronoi-Features enthalten ist
                geom_wkt = feature.geometry().asWkt()
                attrs_key = str(feature.attributes())
                feature_key = f"{geom_wkt}_{attrs_key}"

                if feature_key in remaining_voronoi_features:
                    thinned_features.append(feature)
                # Wenn nicht in remaining_voronoi_features: wurde gelöscht, also nicht hinzufügen

        return thinned_features

    def _write_final_results(
        self,
        all_created_features,
        thinned_features,
        final_sinks,
        all_extracted_sink,
        feedback,
        progress_start,
        progress_end,
    ):
        """
        Write final results to output sinks.

        Args:
            all_created_features: All created features (before thinning)
            thinned_features: Features that survived Voronoi thinning
            final_sinks: Output sinks for final importance classes (0, 2, 3)
            all_extracted_sink: Output sink for all extracted points
            feedback: Processing feedback object
            progress_start: Start percentage for progress bar
            progress_end: End percentage for progress bar
        """
        feedback.pushInfo("Writing final results...")

        # 1. Schreibe alle extrahierten Punkte (vor Thinning) in den "all_extracted" Layer
        feedback.pushInfo("Writing all extracted points...")
        for feature in all_created_features:
            all_extracted_sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)

        # 2. Schreibe finale ausgedünnte Punkte (nur Importance 0, 2, 3)
        feedback.pushInfo("Writing final thinned points...")
        progress_range = progress_end - progress_start
        total_features = len(thinned_features)

        for current, feature in enumerate(thinned_features):
            importance = feature.attributes()[-1]  # Importance ist das letzte Attribut

            # Nur Importance 0, 2, 3 zu den finalen Layern hinzufügen
            if (
                importance in [0, 2, 3]
                and importance in final_sinks
                and final_sinks[importance] is not None
            ):
                final_sinks[importance].addFeature(
                    feature, QgsFeatureSink.Flag.FastInsert
                )

            # Update progress
            if total_features > 0:
                progress = progress_start + (current / total_features) * progress_range
                feedback.setProgress(int(progress))

    def name(self):
        return "extract_shadow_ip"

    def displayName(self):
        return self.tr("Extract Shadow IP")

    def group(self):
        return self.tr("Custom Scripts")

    def groupId(self):
        return "customscripts"

    def shortHelpString(self):
        return self.tr("Extraktion von Schatten-IP aus Gebäudedaten als Polygone.")

    def tr(self, string):
        """
        Translate a given string using QGIS translation mechanism.

        :param string: text to translate
        :type string: str
        :return: translated text
        :rtype: str
        """
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        """
        Create a new instance of the algorithm.

        :return: new algorithm instance
        :rtype: extract_shadow_ip
        """
        return extract_shadow_ip()
