# App R8 rules. The Flutter Gradle plugin adds this file to the release build
# when it exists (after proguard-android-optimize.txt and its own rules).

# WorkManager 2.10.1 (V4 WP1.4 intake) brings Room 2.6.1, whose consumer rule
# keeps RoomDatabase subclasses but not their no-argument constructors. R8 full
# mode (the AGP 8+ default) does not keep a kept class's default constructor,
# so it removed WorkDatabase_Impl.<init>() and every release build crashed at
# start-up (androidx.startup -> WorkManagerInitializer -> NoSuchMethodException).
# Room creates the generated *_Impl class by reflection, so keep the constructor.
-keep class * extends androidx.room.RoomDatabase { void <init>(); }
