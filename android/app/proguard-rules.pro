# The web interface calls these methods by name through addJavascriptInterface.
-keepclassmembers class com.ugvuav.gcs.MainActivity$Api {
    @android.webkit.JavascriptInterface <methods>;
}
