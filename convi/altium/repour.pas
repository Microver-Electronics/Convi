{ Convi: pour all polygons of a finished PcbDoc and save it.
  Runs as a separate pass after the builder, because pouring inside the builder script sees stale
  state for flipped (bottom) pads.  Altium pours a polygon around whatever copper already exists and
  pours polygons on its own when they are added, so every polygon is removed and re-added (a copy)
  in PourIndex order (= KiCad zone priority, highest first): each pour then sees exactly the
  higher-priority fills, like KiCad's zone filler.
  Placeholders: %PCB% %LOG% %DONE% %STARTED% %SKIP% (|name|name| of KiCad zones that are not filled)
  %NONECK% (|name|name| of polygons poured without neck removal)
  %NECKON% (|name|name| of polygons whose neck removal is switched back on, at the minimum width)
  %NECKLOW% (|name|name| of polygons whose neck limit is 0.9 x the minimum width)
  %NECKHALF% (|name|name| of polygons whose neck limit is 0.5 x the minimum width) }

function ContourArea(C: Variant): Double;
var i, n, j: Integer; S: Double;
begin
  S := 0;
  n := C.Count;
  for i := 0 to n - 1 do
  begin
    j := (i + 1) mod n;
    S := S + (C.x[i] * 1.0) * C.y[j] - (C.x[j] * 1.0) * C.y[i];
  end;
  Result := Abs(S) / 2;
end;

{ poured copper of a solid polygon in mm2 x 1000 (as pcbcheck.pas): read here so that the pipeline can judge a
  re-pour without dumping the whole board again }
function PolyCopper(P: Variant): Integer;
var GI, R: Variant; A: Double; k: Integer;
begin
  Result := -1;
  try
    A := 0;
    GI := P.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(eRegionObject));
    R := GI.FirstPCBObject;
    while R <> Nil do
    begin
      A := A + ContourArea(R.MainContour);
      for k := 0 to R.HoleCount - 1 do
        A := A - ContourArea(R.Holes[k]);
      R := GI.NextPCBObject;
    end;
    P.GroupIterator_Destroy(GI);
    Result := Round(A * 0.00000000000645160 * 1000);
  except
    Result := -1;
  end;
end;

procedure Main;
var D, B, It, O, C: Variant; Lg, Mk: TStringList; Lst, Cp: TInterfaceList; i, best, n: Integer;
begin
  Mk := TStringList.Create;
  Mk.Add('started');
  Mk.SaveToFile('%STARTED%');
  Lg := TStringList.Create;
  try
    // start from the saved file: a board left open by an earlier pass (DRC markers, re-added polygons)
    // poured with stale rule matches and ran no DRC (zone clearances lost, a pour emptied)
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
    Lst := TInterfaceList.Create;
    Cp := TInterfaceList.Create;
    It := B.BoardIterator_Create;
    It.AddFilter_ObjectSet(MkSet(ePolyObject));
    It.AddFilter_LayerSet(AllLayers);
    It.AddFilter_Method(eProcessAll);
    O := It.FirstPCBObject;
    while O <> Nil do
    begin
      Lst.Add(O);
      O := It.NextPCBObject;
    end;
    B.BoardIterator_Destroy(It);
    PCBServer.PreProcess;
    for i := 0 to Lst.Count - 1 do
    begin
      O := Lst.Items[i];
      C := O.Replicate;
      C.Name := O.Name;
      C.PourIndex := O.PourIndex;
      Cp.Add(C);
      B.RemovePCBObject(O);
    end;
    PCBServer.PostProcess;
    n := 0;
    while Cp.Count > 0 do
    begin
      best := 0;
      for i := 1 to Cp.Count - 1 do
        if Cp.Items[i].PourIndex < Cp.Items[best].PourIndex then best := i;
      C := Cp.Items[best];
      B.AddPCBObject(C);
      if Pos('|' + C.Name + '|', '%NONECK%') > 0 then
      begin
        C.RemoveNarrowNecks := False;
        Lg.Add('neck removal off: ' + C.Name);
      end
      else if Pos('|' + C.Name + '|', '%NECKLOW%') > 0 then
      begin
        C.RemoveNarrowNecks := True;
        C.NeckWidthThreshold := Round(C.MinTrack * 0.9);
        Lg.Add('neck limit 0.9: ' + C.Name);
      end
      else if Pos('|' + C.Name + '|', '%NECKHALF%') > 0 then
      begin
        C.RemoveNarrowNecks := True;
        C.NeckWidthThreshold := Round(C.MinTrack * 0.5);
        Lg.Add('neck limit 0.5: ' + C.Name);
      end
      else if Pos('|' + C.Name + '|', '%NECKON%') > 0 then
      begin
        C.RemoveNarrowNecks := True;
        C.NeckWidthThreshold := C.MinTrack;
      end;
      if Pos('|' + C.Name + '|', '%SKIP%') = 0 then
      begin
        C.SetState_CopperPourInvalid;
        C.Rebuild;
        C.SetState_CopperPourValid;
        n := n + 1;
      end;
      Cp.Delete(best);
    end;
    B.ViewManager_FullUpdate;
    It := B.BoardIterator_Create;
    It.AddFilter_ObjectSet(MkSet(ePolyObject));
    It.AddFilter_LayerSet(AllLayers);
    It.AddFilter_Method(eProcessAll);
    O := It.FirstPCBObject;
    while O <> Nil do
    begin
      Lg.Add('AREA' + #9 + O.Name + #9 + IntToStr(PolyCopper(O)));
      O := It.NextPCBObject;
    end;
    B.BoardIterator_Destroy(It);
    D.DoFileSave('');
    Client.CloseDocument(D);
    Lg.Add('poured ' + IntToStr(n));
  except
    Lg.Add('FATAL: ' + ExceptionMessage);
  end;
  Lg.SaveToFile('%LOG%');
  Mk.SaveToFile('%DONE%');
end;
