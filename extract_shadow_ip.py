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
    QgsWkbTypes,
    QgsField,
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

        # Output layers für verschiedene Importance-Klassen
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_importance_3", "Definitiv benötigt Gebäude"
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_importance_2", "Weitere Prüfung erforderlich"
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "output_importance_1", "Nicht benötigte Gebäude"
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink("output_importance_0", "Zuordnungsfehler")
        )

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        """
        Processing
        """

        # Retrieve the inputs
        input_buildings = self.parameterAsSource(
            parameters, self.input_buildings, context
        )
        input_zb_wea = self.parameterAsSource(parameters, self.input_zb_wea, context)

        # If input was not found, throw an exception
        if input_buildings is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.input_buildings)
            )

        if input_zb_wea is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.input_zb_wea)
            )

        # Felder kopieren und 'importance' hinzufügen
        fields = input_buildings.fields()
        fields.append(QgsField("importance", QVariant.Int))

        # Create sinks for each importance class
        sinks = {}
        dest_ids = {}

        for importance in [0, 1, 2, 3]:
            (sink, dest_id) = self.parameterAsSink(
                parameters,
                f"output_importance_{importance}",
                context,
                fields,  # mit importance-Feld
                QgsWkbTypes.Point,
                input_buildings.sourceCrs(),
            )
            sinks[importance] = sink
            dest_ids[importance] = dest_id

        # If sink was not created, throw an exception to indicate that the algorithm
        # encountered a fatal error. The exception text can be any string, but in this
        # case we use the pre-built invalidSinkError method to return a standard
        # helper text for when a sink cannot be evaluated
        if sink is None:
            raise QgsProcessingException(
                self.invalidSinkError(parameters, self.output_ip)
            )

        # Compute the number of steps to display within the progress bar and
        # get features from source
        total = (
            100.0 / input_buildings.featureCount()
            if input_buildings.featureCount()
            else 0
        )
        building_features = list(input_buildings.getFeatures())

        # Send some information to the user
        feedback.pushInfo("Adding building type categories")
        feedback.pushInfo("3 = definitly needed")
        feedback.pushInfo("2 = maybe needed")
        feedback.pushInfo("1 = definitely not needed")
        feedback.pushInfo("0 = unknown (gfkzshh missing/could'nt be matched)")

        # Setup Categorie Codes
        yes = [
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
            1310,
            1311,
            1312,
            2020,
            2030,
            2040,
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
            2732,
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
            3030,
            3031,
            3034,
            3037,
            3040,
            3041,
            3042,
            3043,
            3044,
            3045,
            3046,
            3047,
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
        no = [
            1313,
            1610,
            2060,
            2092,
            2093,
            2094,
            2095,
            2140,
            2141,
            2142,
            2143,
            2210,
            2211,
            2212,
            2213,
            2220,
            2442,
            2444,
            2460,
            2461,
            2462,
            2463,
            2464,
            2465,
            2612,
            2621,
            2721,
            2723,
            2724,
            2726,
            2727,
            2728,
            3074,
            3262,
            3264,
        ]
        maybe = [
            2000,
            2010,
            2050,
            2051,
            2052,
            2053,
            2054,
            2055,
            2056,
            2091,
            2100,
            2110,
            2111,
            2112,
            2113,
            2114,
            2120,
            2121,
            2131,
            2150,
            2160,
            2170,
            2171,
            2172,
            2200,
            2400,
            2410,
            2412,
            2420,
            2422,
            2423,
            2424,
            2430,
            2431,
            2440,
            2441,
            2443,
            2450,
            2451,
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
            2620,
            2622,
            2623,
            2700,
            2720,
            2729,
            2735,
            2740,
            2741,
            2742,
            3032,
            3033,
            3035,
            3036,
            3038,
            3070,
            3080,
            3081,
            3082,
            3090,
            3091,
            3092,
            3094,
            3095,
            3097,
            3098,
            3210,
            3211,
            3212,
            3220,
            3221,
            3222,
            3230,
            3260,
            3261,
            3263,
            3270,
            3271,
            3272,
            3273,
            3280,
            3281,
            3290,
            9998,
        ]
        # Add extra parameter to each input_building-Feature.
        # Based on the attribute "gebnutzbez", a new attribute should be calculated
        # Berechne importance für jedes Gebäude und speichere in Liste
        importance_list = []
        for current, building_feature in enumerate(building_features):
            gfkzshh = (
                str(building_feature.attribute("gfkzshh"))
                if building_feature.attribute("gfkzshh") is not None
                else ""
            )
            importance = 0  # Fallback if gfkzshh is missing
            for code in yes:
                if gfkzshh.endswith(str(code)):
                    importance = 3
                    break
            if importance == 0:
                for code in no:
                    if gfkzshh.endswith(str(code)):
                        importance = 1
                        break
            if importance == 0:
                for code in maybe:
                    if gfkzshh.endswith(str(code)):
                        importance = 2
                        break
            importance_list.append(importance)
            feedback.setProgress(int((current * total) / 2))

        feedback.pushInfo("Extracting nearest point to WEA")
        for current, building_feature in enumerate(building_features):
            # Create point on every line in the polygon
            points = []
            geom = building_feature.geometry()
            if geom.isMultipart():
                for part in geom.asMultiPolygon():
                    for ring in part:  # ring ist eine Liste von QgsPointXY
                        for i in range(len(ring) - 1):
                            line = QgsGeometry.fromPolylineXY([ring[i], ring[i + 1]])
                            midpoint = line.interpolate(line.length() / 2)
                            points.append(midpoint)
            else:
                for ring in geom.asPolygon():
                    for i in range(len(ring) - 1):
                        line = QgsGeometry.fromPolylineXY([ring[i], ring[i + 1]])
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
                importance = importance_list[current]  # Verwende den berechneten Wert aus der Liste
                attrs = building_feature.attributes() + [importance]
                feature.setAttributes(attrs)

                # Füge Feature zum entsprechenden Sink hinzu
                if importance in sinks and sinks[importance] is not None:
                    sinks[importance].addFeature(
                        feature, QgsFeatureSink.Flag.FastInsert
                    )

            # Update the progress bar
            feedback.setProgress(int((current * total) / 2) + 50)

        # Implement in the future:
        # Filter the given buildings by ATKIS OB-Katalog gebaeudenutzung
        # Divide all buildings into "not needed", "needed" and "further check needed"
        # Calculate Voronoi-Polygons
        # Remove all IP with voronoi polygons smaller than a theshold
        #   - start deleting "further check needed"
        #   - then start deleting points with no adress
        #   - lastly delete also other points
        #   -> until the voronoi polygons are no longer smaller than the threshold

        # Return the results of the algorithm. In this case our only result is
        # the feature sink which contains the processed features, but some
        # algorithms may return multiple feature sinks, calculated numeric
        # statistics, etc. These should all be included in the returned
        # dictionary, with keys matching the feature corresponding parameter
        # or output names.
        return {
            "output_importance_0": dest_ids[0],
            "output_importance_1": dest_ids[1],
            "output_importance_2": dest_ids[2],
            "output_importance_3": dest_ids[3],
        }

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
