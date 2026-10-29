import Foundation
@main struct Checks {
 static func main() {
  for id in ["com.apple.Terminal","com.googlecode.iterm2","com.apple.Passwords","com.apple.keychainaccess","com.apple.systempreferences","com.example.squipi","com.example.own"] { precondition(!MacAccessPolicy.permits(bundleID:id,ownBundleID:"com.example.own")) }
  precondition(MacAccessPolicy.permits(bundleID:"com.apple.TextEdit",ownBundleID:"com.example.own"))
  precondition(MacAccessPolicy.secure(role:"AXTextField",subrole:"AXSecureTextField",protected:false))
  precondition(MacAccessPolicy.secure(role:"AXTextField",subrole:"",protected:true))
  precondition(!MacAccessPolicy.secure(role:"AXTextField",subrole:"",protected:false))
  precondition(!MacAccessPolicy.pressRoles.contains("AXTextField"))
  precondition(!MacAccessPolicy.textRoles.contains("AXButton"))
  print("PASS: Mac target exclusions, protected-field rejection and distinct press/text role policies")
 }
}
