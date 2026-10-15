from collections.abc import Mapping
from typing import Any

import streamlit as st


_GOOGLE_SIGN_IN = st.components.v2.component(
    "financas_firebase.google_sign_in",
    html="""
<div>
  <button id="google-sign-in" type="button">Entrar com Google</button>
  <button id="google-sign-out" type="button" hidden>Sair</button>
  <span id="google-auth-status" role="status">Restaurando sessão Firebase...</span>
</div>
""",
    js="""
export default function (component) {
  const { data, parentElement, setStateValue } = component
  const root = parentElement.querySelector("#google-sign-in")?.parentElement
  if (!root || root.dataset.initialized === "true") return
  root.dataset.initialized = "true"

  const signInButton = root.querySelector("#google-sign-in")
  const signOutButton = root.querySelector("#google-sign-out")
  const status = root.querySelector("#google-auth-status")
  let lastToken = null

  const sendAuthState = (state) => {
    const signature = JSON.stringify(state)
    if (root.dataset.authState !== signature) {
      root.dataset.authState = signature
      setStateValue("auth", state)
    }
  }

  const showError = (code, message) => {
    status.textContent = message
    sendAuthState({ idToken: "", error: code })
  }

  const initialize = async () => {
    const config = data?.firebaseConfig
    if (!config?.apiKey || !config?.authDomain || !config?.projectId || !config?.appId) {
      showError(
        "firebase/web-config-missing",
        "A configuração do aplicativo Web Firebase está incompleta."
      )
      return
    }

    status.textContent = "Restaurando sessão Firebase..."
    try {
      const [appSdk, authSdk] = await Promise.all([
        import("https://www.gstatic.com/firebasejs/11.10.0/firebase-app.js"),
        import("https://www.gstatic.com/firebasejs/11.10.0/firebase-auth.js"),
      ])
      const existingApp = appSdk.getApps().find((app) => app.name === "financas-streamlit")
      const app = existingApp ?? appSdk.initializeApp(config, "financas-streamlit")
      const auth = authSdk.getAuth(app)
      const provider = new authSdk.GoogleAuthProvider()
      await authSdk.setPersistence(auth, authSdk.browserLocalPersistence)

      signInButton.onclick = async () => {
        signInButton.disabled = true
        status.textContent = "Conectando ao Google..."
        try {
          await authSdk.signInWithPopup(auth, provider)
        } catch (error) {
          const code = error?.code ?? "auth/sign-in-failed"
          const messages = {
            "auth/popup-blocked": "O navegador bloqueou o login. Permita pop-ups e tente novamente.",
            "auth/popup-closed-by-user": "O login foi cancelado.",
            "auth/unauthorized-domain": "Este endereço não está autorizado no Firebase Authentication.",
            "auth/operation-not-allowed": "O provedor Google não está habilitado no Firebase Authentication.",
          }
          status.textContent = messages[code] ?? `Falha no login Firebase (${code}).`
          sendAuthState({ idToken: "", error: code })
        } finally {
          signInButton.disabled = false
        }
      }

      signOutButton.onclick = async () => {
        try {
          await authSdk.signOut(auth)
        } catch (error) {
          const code = error?.code ?? "auth/sign-out-failed"
          showError(code, `Não foi possível encerrar a sessão Firebase (${code}).`)
        }
      }

      authSdk.onIdTokenChanged(auth, async (user) => {
        if (!user) {
          lastToken = null
          signInButton.disabled = false
          signInButton.hidden = false
          signOutButton.hidden = true
          status.textContent = "Entre com sua conta Google para continuar."
          sendAuthState({ idToken: "", error: "" })
          return
        }

        signInButton.hidden = true
        signOutButton.hidden = false
        status.textContent = `Conectado como ${user.email ?? "conta Google"}`
        try {
          const idToken = await user.getIdToken()
          if (idToken !== lastToken) {
            lastToken = idToken
            sendAuthState({ idToken, error: "" })
          }
        } catch (error) {
          const code = error?.code ?? "auth/token-failed"
          showError(code, `Não foi possível atualizar sua sessão Firebase (${code}).`)
        }
      })
    } catch (error) {
      const code = error?.code ?? "auth/firebase-init-failed"
      showError(
        code,
        `Não foi possível iniciar a autenticação Firebase (${code}).`,
      )
    }
  }

  initialize()
}
""",
)


def get_firebase_web_config() -> dict[str, str]:
    try:
        settings = st.secrets["firebase"]
        config = {
            "apiKey": str(settings["api_key"]).strip(),
            "authDomain": str(settings["auth_domain"]).strip(),
            "projectId": str(settings["project_id"]).strip(),
            "appId": str(settings["app_id"]).strip(),
        }
    except (FileNotFoundError, KeyError, TypeError) as exc:
        raise ValueError(
            "Configure api_key, auth_domain, project_id e app_id "
            "na seção [firebase] de .streamlit/secrets.toml."
        ) from exc

    if any(not value for value in config.values()):
        raise ValueError(
            "Os campos da configuração do app Web Firebase não podem ficar vazios."
        )
    return config


def google_sign_in(
    firebase_config: Mapping[str, Any], *, key: str
) -> dict[str, str]:
    component_state = _GOOGLE_SIGN_IN(
        key=key,
        data={"firebaseConfig": dict(firebase_config)},
        default={"auth": {"idToken": "", "error": ""}},
        on_auth_change=lambda: None,
    )
    auth_state = component_state.auth
    if not isinstance(auth_state, dict):
        return {"idToken": "", "error": ""}
    return {
        "idToken": str(auth_state.get("idToken", "")),
        "error": str(auth_state.get("error", "")),
    }
