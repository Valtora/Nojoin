import api from "./client";

// OAuth 2.1 consent flow for the MCP connector (docs/MCP.md). The consent
// page validates the client's authorization request, then submits the
// signed-in user's decision; connected apps are managed from settings.

export interface OAuthAuthorizeParams {
  client_id: string;
  redirect_uri: string;
  response_type?: string;
  scope?: string;
  state?: string;
  code_challenge?: string;
  code_challenge_method?: string;
  resource?: string;
}

export interface OAuthAuthorizeInfo {
  client_name: string;
  scope: string;
  scope_items: string[];
  redirect_uri: string;
}

export interface OAuthDecisionResult {
  redirect_to: string;
}

export interface ConnectedApp {
  grant_id: string;
  client_name: string;
  scope: string;
  created_at: string;
  last_used_at: string | null;
}

export const getOAuthAuthorizeInfo = async (
  params: OAuthAuthorizeParams,
): Promise<OAuthAuthorizeInfo> => {
  const response = await api.get<OAuthAuthorizeInfo>("/oauth/authorize/info", {
    params: {
      client_id: params.client_id,
      redirect_uri: params.redirect_uri,
      response_type: params.response_type ?? "code",
      scope: params.scope,
      code_challenge: params.code_challenge,
      code_challenge_method: params.code_challenge_method,
    },
  });
  return response.data;
};

export const submitOAuthDecision = async (
  params: OAuthAuthorizeParams,
  approve: boolean,
): Promise<OAuthDecisionResult> => {
  const response = await api.post<OAuthDecisionResult>(
    "/oauth/authorize/decision",
    {
      approve,
      client_id: params.client_id,
      redirect_uri: params.redirect_uri,
      response_type: params.response_type ?? "code",
      scope: params.scope,
      state: params.state,
      code_challenge: params.code_challenge,
      code_challenge_method: params.code_challenge_method,
      resource: params.resource,
    },
  );
  return response.data;
};

export const getConnectedApps = async (): Promise<ConnectedApp[]> => {
  const response = await api.get<ConnectedApp[]>("/oauth/grants");
  return response.data;
};

export const revokeConnectedApp = async (grantId: string): Promise<void> => {
  await api.delete(`/oauth/grants/${grantId}`);
};
