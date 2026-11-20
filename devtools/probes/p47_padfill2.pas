function FindPad(B: Variant; Des, Num: String): Variant;
var It, O: Variant;
begin
  Result := Nil;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePadObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    if O.InComponent then
      if (O.Component.Name.Text = Des) and (O.Name = Num) then begin Result := O; Break; end;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

function PolyOf(B: Variant; Lyr: Integer): Variant;
var It, G: Variant;
begin
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  Result := Nil;
  while G <> Nil do
  begin
    if (G.Layer = Lyr) and (Copy(G.Name, 1, 3) = 'GND') then Result := G;
    G := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure Body;
var D, B, It, P, G, GI, R: Variant; i, best, dist, nreg: Integer; Des: String; Lst: TStringList;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  Lst := TStringList.Create;
  Lst.CommaText := 'C37-2,C36-2,C35-2,C34-2,C13-2,D25-1,C30-2,D23-1,C15-2,D3-1';
  for i := 0 to Lst.Count - 1 do
  begin
    Des := Lst[i];
    P := FindPad(B, Copy(Des, 1, Pos('-', Des) - 1), Copy(Des, Pos('-', Des) + 1, 5));
    G := PolyOf(B, P.Layer);
    best := 999999999; nreg := 0;
    GI := G.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(eRegionObject, eTrackObject, eArcObject));
    R := GI.FirstPCBObject;
    while R <> Nil do
    begin
      nreg := nreg + 1;
      dist := B.PrimPrimDistance(P, R);
      if dist < best then best := dist;
      R := GI.NextPCBObject;
    end;
    G.GroupIterator_Destroy(GI);
    L(Des + ' ' + G.Name + ' pad net=' + P.Net.Name + ' minDist(mil)=' + FloatToStr(CoordToMils(best)) + ' parts=' + IntToStr(nreg));
  end;
  D.SetModified(False);
end;
