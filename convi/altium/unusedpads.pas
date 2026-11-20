{ Convi: remove the pad shapes of vias on layers where nothing connects, as KiCad's 'remove unused layers'
  does, with Altium's own Tools > Remove Unused Pad Shapes (the script API has no per-layer call).
  Runs after the polygons are poured (a via in a pour keeps its pad there), before the final re-pour.
  The tool's dialog is answered by Convi (runner: scope Vias, Remove unused, start/end layers kept as
  %PRESERVE%, not 'Selected only').
  Placeholders: %PCB% %LOG% %DONE% %STARTED% %PRESERVE% }

procedure Main;
var D, B: Variant; Lg, Mk: TStringList;
begin
  Mk := TStringList.Create;
  Mk.Add('started');
  Mk.SaveToFile('%STARTED%');
  Lg := TStringList.Create;
  try
    try
      D := Client.GetDocumentByPath('%PCB%');
      if D <> Nil then
      begin
        D.SetModified(False);
        Client.CloseDocument(D);
      end;
    except
    end;
    D := Client.OpenDocument('PCB', '%PCB%');
    Client.ShowDocument(D);
    B := PCBServer.GetCurrentPCBBoard;
    ResetParameters;
    RunProcess('PCB:RemoveUnusedPads');
    B.ViewManager_FullUpdate;
    D.DoFileSave('');
    Client.CloseDocument(D);
    Lg.Add('unused pad shapes removed');
  except
    Lg.Add('FATAL: ' + ExceptionMessage);
  end;
  Lg.SaveToFile('%LOG%');
  Mk.SaveToFile('%DONE%');
end;
