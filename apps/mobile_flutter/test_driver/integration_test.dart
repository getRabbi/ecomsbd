// Host side of `flutter drive` for integration_test/: saves the screenshots
// the test takes and its step report under the repo's git-ignored qa-output/.

import 'dart:convert';
import 'dart:io';

import 'package:integration_test/integration_test_driver_extended.dart';

Future<void> main() async {
  final out = Directory('../../qa-output/mobile')..createSync(recursive: true);
  await integrationDriver(
    onScreenshot: (name, bytes, [args]) async {
      File('${out.path}/$name.png').writeAsBytesSync(bytes);
      return true;
    },
    responseDataCallback: (data) async {
      File('${out.path}/report.json').writeAsStringSync(
        const JsonEncoder.withIndent('  ').convert(data),
      );
    },
    writeResponseOnFailure: true,
  );
}
