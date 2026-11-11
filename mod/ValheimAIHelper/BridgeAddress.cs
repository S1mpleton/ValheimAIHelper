using System;

namespace ValheimAIHelper;

public static class BridgeAddress
{
    public static bool IsValid(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri) &&
        uri.Scheme == "http" && uri.IsLoopback && string.IsNullOrEmpty(uri.UserInfo) &&
        (uri.AbsolutePath == "/" || uri.AbsolutePath == "") && string.IsNullOrEmpty(uri.Query) && string.IsNullOrEmpty(uri.Fragment);
}
