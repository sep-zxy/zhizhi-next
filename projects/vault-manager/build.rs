fn main() {
    println!("cargo:rerun-if-changed=assets/vault-manager.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut resource = winresource::WindowsResource::new();
        resource.set_icon("assets/vault-manager.ico");
        resource.set("FileDescription", "Vault Manager");
        resource.set("ProductName", "Vault Manager");
        resource
            .compile()
            .expect("failed to compile Windows resources");
    }
}
